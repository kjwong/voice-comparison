#!/usr/bin/env python3
"""Detect and remove clicks from MP3 audio files (post-hoc repair).

For each input MP3:
  1. Decode to 16-bit PCM (via pydub/ffmpeg).
  2. Detect sample-level discontinuities using the same threshold logic as
     detect_clicks.py (max(8000, 5x p99 of inter-sample deltas)).
  3. If any clicks found:
       a. Mid-stream: linear-interpolate across the click region using the first
          clean samples on either side.
       b. Start-of-file (|sample[0]| > 1000): apply 5ms linear fade-in.
       c. Re-encode the repaired PCM to 128 kbps MP3 via lameenc.
     If no clicks found: return the original bytes verbatim — no re-encoding,
     so files that were already clean don't pay the lossy generation tax.

Usage:
    # Single file or URL
    python scripts/declick_mp3.py https://...mp3 -o /tmp/clean.mp3

    # Many files into output directory (preserves basename)
    python scripts/declick_mp3.py audio/inworld-comparison/nate/*.mp3 -d /tmp/clean/

    # In-place (DESTRUCTIVE — backups recommended)
    python scripts/declick_mp3.py audio/foo.mp3 --in-place

Exit code: 0 if all files processed successfully (regardless of whether they had
clicks). Non-zero only on actual errors (download failure, decode failure, etc.).
"""

import argparse
import array
import io
import sys
from pathlib import Path

import lameenc
import requests
from pydub import AudioSegment

ABSOLUTE_DELTA_FLOOR = 8000
RELATIVE_DELTA_FACTOR = 5
START_AMP_THRESHOLD = 1000
START_FADE_MS = 5
SAMPLE_RATE = 44100


def fetch(path_or_url: str) -> bytes:
    if path_or_url.startswith(("http://", "https://")):
        resp = requests.get(path_or_url, timeout=20)
        resp.raise_for_status()
        return resp.content
    return Path(path_or_url).read_bytes()


def find_click_regions(samples, threshold: int, gap_tolerance: int = 5):
    """Yield (start_sample, end_sample) inclusive for each contiguous click region.

    A region grows while inter-sample deltas exceed threshold; it terminates after
    `gap_tolerance` consecutive clean samples, so adjacent micro-clicks merge.
    """
    n = len(samples)
    region_start = None
    last_dirty = None
    for i in range(1, n):
        if abs(samples[i] - samples[i - 1]) > threshold:
            if region_start is None:
                region_start = i
            last_dirty = i
        elif region_start is not None and i - last_dirty > gap_tolerance:
            yield region_start, last_dirty
            region_start = None
            last_dirty = None
    if region_start is not None:
        yield region_start, last_dirty


def repair_mid_stream(samples, threshold: int) -> int:
    """Linear-interpolate across click regions. Returns count of regions repaired."""
    repaired = 0
    n = len(samples)
    for start, end in list(find_click_regions(samples, threshold)):
        # Anchor on the clean samples just outside the region
        before_idx = start - 1
        after_idx = end + 1
        if before_idx < 0 or after_idx >= n:
            continue
        before = samples[before_idx]
        after = samples[after_idx]
        span = end - start + 1
        for k in range(span):
            samples[start + k] = int(before + (after - before) * (k + 1) / (span + 1))
        repaired += 1
    return repaired


def repair_start(samples) -> bool:
    """Apply a 5ms linear fade-in if sample[0] is far from zero."""
    window = max(1, int(SAMPLE_RATE * START_FADE_MS / 1000))
    start_amp = max(abs(s) for s in samples[:window])
    if start_amp <= START_AMP_THRESHOLD:
        return False
    for i in range(window):
        samples[i] = int(samples[i] * i / window)
    return True


def pcm_to_mp3_lameenc(pcm_bytes: bytes, sample_rate: int) -> bytes:
    enc = lameenc.Encoder()
    enc.set_bit_rate(128)
    enc.set_in_sample_rate(sample_rate)
    enc.set_channels(1)
    enc.set_quality(2)
    return enc.encode(pcm_bytes) + enc.flush()


def declick(mp3_bytes: bytes) -> tuple[bytes, dict]:
    """Returns (output_bytes, stats). output_bytes == input_bytes if file was clean."""
    audio = AudioSegment.from_file(io.BytesIO(mp3_bytes)).set_channels(1)
    samples = array.array("h")
    samples.frombytes(audio.raw_data)

    # Detect threshold
    deltas_sample = sorted(abs(samples[i] - samples[i - 1]) for i in range(1, len(samples)))
    p99 = deltas_sample[int(len(deltas_sample) * 0.99)] if deltas_sample else 0
    threshold = max(ABSOLUTE_DELTA_FLOOR, RELATIVE_DELTA_FACTOR * p99)

    mid_clicks = sum(1 for _ in find_click_regions(samples, threshold))
    start_window = max(1, int(audio.frame_rate * START_FADE_MS / 1000))
    start_amp = max(abs(s) for s in samples[:start_window])
    has_start_click = start_amp > START_AMP_THRESHOLD

    stats = {
        "duration_s": len(audio) / 1000,
        "frame_rate": audio.frame_rate,
        "p99_delta": p99,
        "threshold": threshold,
        "mid_click_regions": mid_clicks,
        "start_amp": start_amp,
        "had_start_click": has_start_click,
        "repaired": False,
    }

    if mid_clicks == 0 and not has_start_click:
        return mp3_bytes, stats  # already clean — return verbatim

    repair_mid_stream(samples, threshold)
    repair_start(samples)
    repaired_mp3 = pcm_to_mp3_lameenc(samples.tobytes(), audio.frame_rate)
    stats["repaired"] = True
    stats["original_size"] = len(mp3_bytes)
    stats["repaired_size"] = len(repaired_mp3)
    return repaired_mp3, stats


def report(name: str, stats: dict):
    if not stats["repaired"]:
        print(f"  CLEAN — passed through unchanged ({stats['duration_s']:.2f}s)")
        return
    parts = []
    if stats["mid_click_regions"]:
        parts.append(f"{stats['mid_click_regions']} mid-stream region(s)")
    if stats["had_start_click"]:
        parts.append(f"start click ({stats['start_amp']})")
    print(f"  REPAIRED ({', '.join(parts)})")
    print(
        f"    duration: {stats['duration_s']:.2f}s   "
        f"size: {stats['original_size']}→{stats['repaired_size']} bytes   "
        f"threshold: {stats['threshold']}"
    )


def output_path(input_arg: str, out_arg: str | None, dir_arg: str | None,
                in_place: bool) -> Path:
    if in_place:
        if input_arg.startswith(("http://", "https://")):
            raise ValueError("--in-place can't be used with URLs")
        return Path(input_arg)
    if out_arg:
        return Path(out_arg)
    if dir_arg:
        # Use the URL/path basename
        base = input_arg.rsplit("/", 1)[-1]
        if not base.lower().endswith(".mp3"):
            base += ".mp3"
        return Path(dir_arg) / base
    raise ValueError("Specify -o, -d, or --in-place")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", help="MP3 file paths or URLs")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("-o", "--output", help="Output file (single input only)")
    g.add_argument("-d", "--output-dir", help="Output directory")
    g.add_argument("--in-place", action="store_true", help="Overwrite originals")
    args = parser.parse_args()

    if args.output and len(args.files) != 1:
        print("ERROR: -o requires exactly one input. Use -d for many.", file=sys.stderr)
        sys.exit(2)
    if args.output_dir:
        Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    errors = 0
    for f in args.files:
        print(f)
        try:
            mp3_bytes = fetch(f)
            cleaned, stats = declick(mp3_bytes)
            report(f, stats)
            if stats["repaired"] or args.in_place:
                # Write the result. If clean and not in-place, still write a copy.
                out = output_path(f, args.output, args.output_dir, args.in_place)
                out.write_bytes(cleaned)
                print(f"    → {out}")
            elif args.output_dir or args.output:
                # Clean file but user wants a copy in output dir
                out = output_path(f, args.output, args.output_dir, args.in_place)
                out.write_bytes(cleaned)
                print(f"    → {out} (verbatim copy, no clicks)")
        except Exception as e:
            print(f"  FAILED: {e}")
            errors += 1
        print()

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
