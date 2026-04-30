#!/usr/bin/env python3
"""Programmatically detect clicks/snaps in MP3 audio files.

Decodes each input MP3 to 16-bit PCM and flags two distinct artifact types:

  1. Mid-stream click: any consecutive-sample delta exceeding both 8000 (int16) AND
     5x the file's own 99th-percentile delta. Catches sharp impulse artifacts typical
     of MP3 frame-boundary glitches in some encoders.

  2. Start-of-file click: abs(sample[0]) above 1000 indicates a DC-step at playback
     start (the speaker has to instantaneously transition from silence to a non-zero
     amplitude, which produces an audible pop).

Both thresholds are relative-to-the-file — a quiet file with a small click still
trips because the threshold scales with the file's own p99 delta.

Usage:
    python scripts/detect_clicks.py file1.mp3 file2.mp3 ...
    python scripts/detect_clicks.py https://example.com/foo.mp3
    python scripts/detect_clicks.py audio/inworld-comparison/nate/*.mp3

Exit code: 0 if all files clean, 1 if any are dirty (suitable for CI gating).
"""

import argparse
import array
import io
import sys
from pathlib import Path

import requests
from pydub import AudioSegment

ABSOLUTE_DELTA_FLOOR = 8000     # int16 — minimum delta to consider, even on quiet files
RELATIVE_DELTA_FACTOR = 5       # multiplier applied to file's p99 delta
START_AMP_THRESHOLD = 1000      # int16 — |sample[0]| above this = DC-step start click
START_WINDOW_MS = 5             # check first N ms for the DC-step signature


def fetch(path_or_url: str) -> bytes:
    if path_or_url.startswith(("http://", "https://")):
        resp = requests.get(path_or_url, timeout=20)
        resp.raise_for_status()
        return resp.content
    return Path(path_or_url).read_bytes()


def analyze(mp3_bytes: bytes) -> dict:
    audio = AudioSegment.from_file(io.BytesIO(mp3_bytes)).set_channels(1)
    samples = array.array("h")
    samples.frombytes(audio.raw_data)
    n = len(samples)
    if n < 2:
        return {"error": "audio too short"}

    deltas = array.array("i", (abs(samples[i] - samples[i - 1]) for i in range(1, n)))
    deltas_sorted = sorted(deltas)
    p99 = deltas_sorted[int(len(deltas_sorted) * 0.99)]
    p50 = deltas_sorted[int(len(deltas_sorted) * 0.50)]
    threshold = max(ABSOLUTE_DELTA_FLOOR, RELATIVE_DELTA_FACTOR * p99)

    clicks = []
    for i, d in enumerate(deltas, start=1):
        if d > threshold:
            clicks.append((i, d, samples[i - 1], samples[i]))

    start_window = max(1, int(audio.frame_rate * START_WINDOW_MS / 1000))
    start_amp = max(abs(s) for s in samples[:start_window])

    return {
        "duration_s": len(audio) / 1000,
        "n_samples": n,
        "frame_rate": audio.frame_rate,
        "p50_delta": p50,
        "p99_delta": p99,
        "max_delta": max(deltas),
        "threshold": threshold,
        "clicks": clicks,
        "start_amp": start_amp,
    }


def is_dirty(r: dict) -> bool:
    if "error" in r:
        return True
    return bool(r["clicks"]) or r["start_amp"] > START_AMP_THRESHOLD


def report(name: str, r: dict, max_clicks_shown: int = 5):
    if "error" in r:
        print(f"  ERROR: {r['error']}")
        return

    issues = []
    if r["start_amp"] > START_AMP_THRESHOLD:
        issues.append(f"start click ({r['start_amp']})")
    if r["clicks"]:
        issues.append(f"{len(r['clicks'])} mid-stream click(s)")

    verdict = "CLEAN" if not issues else "DIRTY"
    print(f"  {verdict}" + (f"  ({', '.join(issues)})" if issues else ""))
    print(f"    duration:   {r['duration_s']:.2f}s  ({r['n_samples']:,} samples @ {r['frame_rate']}Hz)")
    print(f"    deltas:     median {r['p50_delta']}, p99 {r['p99_delta']}, max {r['max_delta']}")
    print(f"    threshold:  {r['threshold']}")
    print(f"    sample[0]:  {r['start_amp']}")
    if r["clicks"]:
        for i, d, prev, cur in r["clicks"][:max_clicks_shown]:
            t = i / r["frame_rate"]
            print(f"      click @ {t:>6.4f}s   prev={prev:>6}  cur={cur:>6}  Δ={d:>6}")
        if len(r["clicks"]) > max_clicks_shown:
            print(f"      ... and {len(r['clicks']) - max_clicks_shown} more")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", help="MP3 file paths or URLs")
    args = parser.parse_args()

    any_dirty = False
    for f in args.files:
        print(f)
        try:
            r = analyze(fetch(f))
            report(f, r)
            if is_dirty(r):
                any_dirty = True
        except Exception as e:
            print(f"  FAILED: {e}")
            any_dirty = True
        print()

    sys.exit(1 if any_dirty else 0)


if __name__ == "__main__":
    main()
