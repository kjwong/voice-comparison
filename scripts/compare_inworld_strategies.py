#!/usr/bin/env python3
"""Compare Inworld TTS post-processing strategies for clicking artifacts AND latency.

Generates the same text via Inworld TTS using four strategies, saves each output
side-by-side for A/B listening, and times each stage so you can decide whether the
clean-PCM path is worth its latency cost in production.

Strategies:

1. raw                  — Inworld MP3 saved verbatim (matches backend production).
2. mp3_reencoded        — Inworld MP3 decoded and re-encoded via pydub/LAME at 128k.
                          Diagnostic only — requires ffmpeg, not portable to App Engine
                          Standard / minimal-image runtimes.
3. linear16_to_mp3      — Inworld LINEAR16/PCM, encoded directly via lameenc at 128k.
                          Risks a DC-step click at sample 0 because the first PCM
                          sample is not guaranteed to be zero.
4. linear16_padded      — Same as #3 but prepends 20ms of silence and applies a 5ms
                          linear fade-in to the start of the PCM before encoding.
                          Fixes the boundary click at sub-millisecond latency cost.

Production portability:
    - Strategies 1 and 3 work on GCE Standard / App Engine Standard / Cloud Run /
      Cloud Functions. lameenc is a pure pip install with manylinux wheels — no
      ffmpeg or apt-get required.
    - Strategy 2 requires ffmpeg in the deploy image, which most managed runtimes
      don't provide out of the box.

Latency reporting per strategy:
    - api_time:     wall-clock from request send to full response body received.
    - process_time: local CPU time spent decoding/encoding (0 for raw).
    - total:        api_time + process_time (production end-to-end).

Run multiple iterations to get a sense of variance:
    python scripts/compare_inworld_strategies.py --iterations 3

Reads INWORLD_API_KEY from .env at the project root.
"""

import argparse
import array
import base64
import io
import os
import statistics
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from pydub import AudioSegment

try:
    import lameenc
    HAS_LAMEENC = True
except ImportError:
    HAS_LAMEENC = False

load_dotenv()

ROOT = Path(__file__).parent.parent
OUT_DIR = ROOT / "audio" / "inworld-comparison"
TEXT = "Nice! What kind of special powers would your wands have?"
DEFAULT_VOICES = ["Lauren", "Jessica", "Brian", "Nate"]
MODEL = "inworld-tts-1.5-max"
SAMPLE_RATE = 44100


def call_inworld(voice_id: str, encoding: str) -> tuple[bytes, float]:
    """encoding: 'MP3' or 'LINEAR16'. Returns (audio_bytes, api_seconds)."""
    payload = {
        "text": TEXT,
        "voiceId": voice_id,
        "modelId": MODEL,
        "audioConfig": {
            "audioEncoding": encoding,
            "sampleRateHertz": SAMPLE_RATE,
        },
    }
    if encoding == "MP3":
        payload["audioConfig"]["bitRate"] = 128000

    t0 = time.perf_counter()
    resp = requests.post(
        "https://api.inworld.ai/tts/v1/voice",
        json=payload,
        headers={
            "Authorization": f"Basic {os.environ['INWORLD_API_KEY']}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    audio = base64.b64decode(resp.json()["audioContent"])
    api_seconds = time.perf_counter() - t0
    return audio, api_seconds


def pcm_to_mp3_lameenc(pcm_bytes: bytes) -> bytes:
    """Encode 16-bit mono PCM bytes to a 128 kbps MP3 using lameenc (no ffmpeg)."""
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(128)
    encoder.set_in_sample_rate(SAMPLE_RATE)
    encoder.set_channels(1)
    encoder.set_quality(2)  # 0=best/slow, 9=worst/fast; 2 is the LAME --abr default
    return encoder.encode(pcm_bytes) + encoder.flush()


def pad_and_fade_pcm(pcm_bytes: bytes, silence_ms: int = 20, fade_ms: int = 5) -> bytes:
    """Prepend silence and apply a linear fade-in to avoid a DC-step click at sample 0."""
    samples = array.array("h")
    samples.frombytes(pcm_bytes)
    samples_per_ms = SAMPLE_RATE // 1000
    fade_samples = min(fade_ms * samples_per_ms, len(samples))
    for i in range(fade_samples):
        samples[i] = int(samples[i] * i / fade_samples)
    silence = array.array("h", [0] * (silence_ms * samples_per_ms))
    return (silence + samples).tobytes()


def fmt_ms(s: float) -> str:
    return f"{s * 1000:6.0f}ms"


def fmt_kb(n: int) -> str:
    return f"{n // 1024:>4}KB"


def run_once(voice: str, save_to: Path | None) -> dict:
    results: dict[str, dict] = {}

    # Strategy 1: raw Inworld MP3.
    mp3_bytes, mp3_api = call_inworld(voice, "MP3")
    if save_to:
        (save_to / "1_raw.mp3").write_bytes(mp3_bytes)
    results["1_raw"] = {
        "api": mp3_api,
        "process": 0.0,
        "total": mp3_api,
        "size": len(mp3_bytes),
    }

    # Strategy 2: decode + re-encode the MP3 we already have (pydub/ffmpeg).
    t0 = time.perf_counter()
    audio = AudioSegment.from_file(io.BytesIO(mp3_bytes))
    if save_to:
        audio.export(str(save_to / "2_mp3_reencoded.mp3"), format="mp3", bitrate="128k")
        out_size = (save_to / "2_mp3_reencoded.mp3").stat().st_size
    else:
        buf = io.BytesIO()
        audio.export(buf, format="mp3", bitrate="128k")
        out_size = len(buf.getvalue())
    process2 = time.perf_counter() - t0
    results["2_mp3_reencoded"] = {
        "api": mp3_api,  # same network call as #1
        "process": process2,
        "total": mp3_api + process2,
        "size": out_size,
    }

    time.sleep(0.5)

    # Strategy 3: request LINEAR16, then encode locally with lameenc.
    if not HAS_LAMEENC:
        results["3_linear16_to_mp3"] = {
            "error": "lameenc not installed — pip install lameenc"
        }
        return results

    try:
        pcm_bytes, pcm_api = call_inworld(voice, "LINEAR16")
    except requests.HTTPError as e:
        body = getattr(e.response, "text", "")[:200] if e.response else ""
        err = f"HTTP {e}: {body}"
        results["3_linear16_to_mp3"] = {"error": err}
        results["4_linear16_padded"] = {"error": err}
        return results
    except Exception as e:
        results["3_linear16_to_mp3"] = {"error": str(e)}
        results["4_linear16_padded"] = {"error": str(e)}
        return results

    # Strategy 3: encode raw PCM directly.
    t0 = time.perf_counter()
    mp3_out = pcm_to_mp3_lameenc(pcm_bytes)
    process3 = time.perf_counter() - t0
    if save_to:
        (save_to / "3_linear16_to_mp3.mp3").write_bytes(mp3_out)
    results["3_linear16_to_mp3"] = {
        "api": pcm_api,
        "process": process3,
        "total": pcm_api + process3,
        "size": len(mp3_out),
        "pcm_size": len(pcm_bytes),
    }

    # Strategy 4: pad + fade, then encode.
    t0 = time.perf_counter()
    padded = pad_and_fade_pcm(pcm_bytes)
    mp3_out = pcm_to_mp3_lameenc(padded)
    process4 = time.perf_counter() - t0
    if save_to:
        (save_to / "4_linear16_padded.mp3").write_bytes(mp3_out)
    results["4_linear16_padded"] = {
        "api": pcm_api,  # same network call as #3
        "process": process4,
        "total": pcm_api + process4,
        "size": len(mp3_out),
        "pcm_size": len(pcm_bytes),
    }

    return results


def aggregate(runs: list[dict]) -> dict:
    out: dict = {}
    for strategy in runs[0]:
        if "error" in runs[0][strategy]:
            out[strategy] = runs[0][strategy]
            continue
        out[strategy] = {}
        for metric in ("api", "process", "total"):
            values = [
                r[strategy][metric] for r in runs if metric in r.get(strategy, {})
            ]
            out[strategy][metric] = statistics.median(values) if values else None
        out[strategy]["size"] = runs[0][strategy].get("size")
        if "pcm_size" in runs[0][strategy]:
            out[strategy]["pcm_size"] = runs[0][strategy]["pcm_size"]
    return out


def print_voice_summary(voice: str, results: dict, iterations: int):
    label = f"{voice}  (median of {iterations} run{'s' if iterations > 1 else ''})"
    print(f"=== {label} ===")
    print(f"  {'strategy':<22}  {'api':>8}  {'process':>8}  {'total':>8}  {'size':>6}")
    for strategy in ("1_raw", "2_mp3_reencoded", "3_linear16_to_mp3", "4_linear16_padded"):
        r = results.get(strategy, {})
        if "error" in r:
            print(f"  {strategy:<22}  ERROR: {r['error']}")
            continue
        extra = ""
        if "pcm_size" in r:
            extra = f"  (PCM transfer: {fmt_kb(r['pcm_size'])})"
        print(
            f"  {strategy:<22}  "
            f"{fmt_ms(r['api'])}  "
            f"{fmt_ms(r['process'])}  "
            f"{fmt_ms(r['total'])}  "
            f"{fmt_kb(r['size'])}{extra}"
        )
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--voices",
        help="Comma-separated Inworld voice names",
        default=",".join(DEFAULT_VOICES),
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Iterations per voice for latency variance (default 1)",
    )
    args = parser.parse_args()
    voices = [v.strip() for v in args.voices.split(",") if v.strip()]

    print(f"Text:       {TEXT!r}")
    print(f"Voices:     {voices}")
    print(f"Iterations: {args.iterations}")
    print(f"Output:     {OUT_DIR}")
    if not HAS_LAMEENC:
        print("lameenc:    NOT installed — Strategy 3 will be skipped")
        print("            install with: pip install lameenc")
    print()

    for voice in voices:
        out = OUT_DIR / voice.lower()
        out.mkdir(parents=True, exist_ok=True)

        runs = []
        for i in range(args.iterations):
            save_to = out if i == 0 else None  # only save MP3s on the first iteration
            try:
                runs.append(run_once(voice, save_to))
            except Exception as e:
                print(f"  iteration {i + 1} FAILED: {e}")
                continue
            time.sleep(0.5)

        if not runs:
            print(f"=== {voice} === all iterations failed\n")
            continue
        print_voice_summary(voice, aggregate(runs), len(runs))

    print(f"Done. Listen at: {OUT_DIR}")


if __name__ == "__main__":
    main()
