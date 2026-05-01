#!/usr/bin/env python3
"""Apply each pipeline-step variant to ONE LINEAR16 generation for true A/B comparison.

Inworld's TTS is non-deterministic — different API calls produce different audio.
To isolate what our processing (declick / pad+fade / lameenc) does versus what's in
Inworld's underlying audio, this script:

1. Makes one LINEAR16 API call to Inworld.
2. Saves the raw PCM as a WAV (for reference / direct PCM listening).
3. Applies several distinct processing variants to that same PCM and writes each
   as an MP3.

Variants saved:
    01_raw_pcm_to_mp3.mp3       — straight lameenc on the source PCM, no other processing
    02_pad_fade_only.mp3        — pad+fade preroll, no declick
    03_declick_only.mp3         — current-tune declick, no pad+fade
    04_full_pipeline.mp3        — declick + pad+fade (matches the backend PR)
    05_declick_old_tune.mp3     — previous declick tune (5×p99 factor, 5-sample gap, 1 pass)

Plus:
    00_source.wav               — the raw LINEAR16 from Inworld, wrapped as WAV

Usage:
    source venv/bin/activate && source .env
    python scripts/compare_pipeline_steps.py
    python scripts/compare_pipeline_steps.py --voice Brian --speed 100
"""

import argparse
import array
import base64
import io
import math
import os
import struct
import time
from pathlib import Path

import lameenc
import requests
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent.parent
SAMPLE_RATE = 44100
DEFAULT_TEXT = "Let's count to ten together: one, two, three, four, five, six, seven, eight, nine, ten."


def call_inworld(voice: str, speed: int, text: str) -> bytes:
    payload = {
        "text": text,
        "voiceId": voice,
        "modelId": "inworld-tts-1.5-max",
        "audioConfig": {
            "audioEncoding": "LINEAR16",
            "sampleRateHertz": SAMPLE_RATE,
            "speakingRate": speed / 100,
        },
    }
    r = requests.post(
        "https://api.inworld.ai/tts/v1/voice",
        json=payload,
        headers={
            "Authorization": f"Basic {os.environ['INWORLD_API_KEY']}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    r.raise_for_status()
    return base64.b64decode(r.json()["audioContent"])


def pcm_to_mp3(pcm_bytes: bytes) -> bytes:
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(128)
    encoder.set_in_sample_rate(SAMPLE_RATE)
    encoder.set_channels(1)
    encoder.set_quality(2)
    return encoder.encode(pcm_bytes) + encoder.flush()


def pcm_to_wav(pcm_bytes: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw PCM in a WAV header so it plays in any audio player."""
    n_samples_bytes = len(pcm_bytes)
    return (
        b"RIFF"
        + struct.pack("<I", 36 + n_samples_bytes)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + struct.pack("<H", 1)  # PCM format
        + struct.pack("<H", 1)  # mono
        + struct.pack("<I", sample_rate)
        + struct.pack("<I", sample_rate * 2)  # byte rate
        + struct.pack("<H", 2)  # block align
        + struct.pack("<H", 16)  # bits per sample
        + b"data"
        + struct.pack("<I", n_samples_bytes)
        + pcm_bytes
    )


def pad_and_fade_pcm(
    pcm_bytes: bytes,
    silence_ms: int = 20,
    fade_ms: int = 5,
    fade_curve: str = "linear",
) -> bytes:
    """fade_curve: 'linear', 'cosine' (Hann window), or 'none' (no fade, just silence)."""
    samples = array.array("h")
    samples.frombytes(pcm_bytes)
    if fade_curve != "none":
        fade_samples = min(round(SAMPLE_RATE * fade_ms / 1000), len(samples))
        if fade_samples > 1:
            if fade_curve == "linear":
                denom = fade_samples - 1
                for i in range(fade_samples):
                    samples[i] = int(samples[i] * i / denom)
            elif fade_curve == "cosine":
                # 0.5 - 0.5*cos(πi/(n-1)): zero slope at both ends, no envelope corner
                denom = fade_samples - 1
                for i in range(fade_samples):
                    multiplier = 0.5 - 0.5 * math.cos(math.pi * i / denom)
                    samples[i] = int(samples[i] * multiplier)
            else:
                raise ValueError(f"unknown fade_curve: {fade_curve}")
    silence_samples = round(SAMPLE_RATE * silence_ms / 1000)
    silence = array.array("h", [0] * silence_samples)
    return (silence + samples).tobytes()


def declick_pcm(
    pcm_bytes: bytes,
    floor: int = 6000,
    factor: int = 4,
    gap_tolerance: int = 50,
    max_iterations: int = 3,
) -> bytes:
    samples = array.array("h")
    samples.frombytes(pcm_bytes)
    n = len(samples)
    if n < 2:
        return pcm_bytes

    for _ in range(max_iterations):
        deltas_sorted = sorted(abs(samples[i] - samples[i - 1]) for i in range(1, n))
        p99 = deltas_sorted[int(len(deltas_sorted) * 0.99)]
        threshold = max(floor, factor * p99)

        repaired = 0
        i = 1
        while i < n:
            if abs(samples[i] - samples[i - 1]) <= threshold:
                i += 1
                continue
            click_start = i
            click_end = i
            gap = 0
            j = i + 1
            while j < n and gap <= gap_tolerance:
                if abs(samples[j] - samples[j - 1]) > threshold:
                    click_end = j
                    gap = 0
                else:
                    gap += 1
                j += 1
            if click_end + 1 < n:
                before = samples[click_start - 1]
                after = samples[click_end + 1]
                count = click_end - click_start + 1
                for k in range(count):
                    samples[click_start + k] = int(
                        before + (after - before) * (k + 1) / (count + 1)
                    )
                repaired += 1
            i = click_end + 1

        if repaired == 0:
            break

    return samples.tobytes()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", default="Jessica")
    parser.add_argument("--speed", type=int, default=75)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument(
        "--out",
        default=str(ROOT / "audio" / "inworld-pipeline-isolated"),
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Calling Inworld: voice={args.voice}, speed={args.speed} → LINEAR16")
    t0 = time.perf_counter()
    pcm = call_inworld(args.voice, args.speed, args.text)
    print(f"  got {len(pcm)//1024}KB PCM in {(time.perf_counter()-t0)*1000:.0f}ms")
    print(f"  output dir: {out_dir}\n")

    declicked = declick_pcm(pcm)

    variants = {
        "00_source.wav": pcm_to_wav(pcm),
        "01_raw_pcm_to_mp3.mp3": pcm_to_mp3(pcm),
        "02_pad_fade_only.mp3": pcm_to_mp3(pad_and_fade_pcm(pcm)),
        "03_declick_only.mp3": pcm_to_mp3(declicked),
        "04_full_pipeline_LINEAR_5ms.mp3": pcm_to_mp3(
            pad_and_fade_pcm(declicked, silence_ms=20, fade_ms=5, fade_curve="linear")
        ),
        "05_declick_old_tune.mp3": pcm_to_mp3(
            pad_and_fade_pcm(
                declick_pcm(pcm, floor=8000, factor=5, gap_tolerance=5, max_iterations=1)
            )
        ),
        # New thump-investigation variants — all use declick + same silence_ms,
        # differing only in fade design.
        "06_no_fade_30ms_silence.mp3": pcm_to_mp3(
            pad_and_fade_pcm(declicked, silence_ms=30, fade_ms=0, fade_curve="none")
        ),
        "07_cosine_5ms_30ms_silence.mp3": pcm_to_mp3(
            pad_and_fade_pcm(declicked, silence_ms=30, fade_ms=5, fade_curve="cosine")
        ),
        "08_cosine_15ms_30ms_silence.mp3": pcm_to_mp3(
            pad_and_fade_pcm(declicked, silence_ms=30, fade_ms=15, fade_curve="cosine")
        ),
    }

    for name, data in variants.items():
        path = out_dir / name
        path.write_bytes(data)
        print(f"  {name}: {len(data)//1024}KB")

    print("\nDone. All variants share the same source PCM — any audible difference is from processing.")


if __name__ == "__main__":
    main()
