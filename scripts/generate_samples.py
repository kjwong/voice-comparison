#!/usr/bin/env python3
"""Generate TTS audio samples for all voices in voices.json.

Usage:
    python scripts/generate_samples.py                          # All voices
    python scripts/generate_samples.py --voices rachel,onyx     # Specific voices
    python scripts/generate_samples.py --providers elevenlabs   # Specific provider

Requires .env in project root:
    ELEVENLABS_API_KEY=...
    OPENAI_API_KEY=...
    AZURE_TTS_API_KEY=...
    GOOGLE_APPLICATION_CREDENTIALS_RAW=...  (base64-encoded service account JSON)
"""

import argparse
import base64
import io
import json
import os
import time
from pathlib import Path

import requests
from pydub import AudioSegment
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).parent.parent
VOICES_JSON = PROJECT_ROOT / "voices.json"
SAMPLE_TYPES = ["sample1", "sample2", "sample3"]
TARGET_DBFS = -20.0


def generate_elevenlabs(voice_id, model, text):
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        json={"text": text, "model_id": model, "output_format": "mp3_44100_128"},
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"],
                 "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content



def generate_azure(voice_id, text):
    region = "southeastasia"
    safe = (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("'", "&apos;").replace('"', "&quot;"))
    ssml = (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="en-US"><voice name="{voice_id}">{safe}</voice></speak>'
    )
    resp = requests.post(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ssml.encode("utf-8"),
        headers={"Ocp-Apim-Subscription-Key": os.environ["AZURE_TTS_API_KEY"],
                 "Content-Type": "application/ssml+xml",
                 "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


def _get_google_tts_client():
    from google.cloud import texttospeech
    from google.oauth2 import service_account
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(base64.b64decode(os.environ["GOOGLE_APPLICATION_CREDENTIALS_RAW"]).decode("utf-8"))
    )
    return texttospeech.TextToSpeechClient(credentials=credentials)


def generate_google(voice_id, text):
    from google.cloud import texttospeech
    client = _get_google_tts_client()
    parts = voice_id.split("-")
    lang = f"{parts[0]}-{parts[1]}"
    response = client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(language_code=lang, name=voice_id),
        audio_config=texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3),
    )
    return response.audio_content


GENERATORS = {
    "elevenlabs": lambda v, t: generate_elevenlabs(v["voiceId"], v["model"], t),
    "azure": lambda v, t: generate_azure(v["voiceId"], t),
    "google": lambda v, t: generate_google(v["voiceId"], t),
}


def normalize(audio_bytes):
    audio = AudioSegment.from_file(io.BytesIO(audio_bytes))
    audio = audio.set_channels(1).set_frame_rate(44100)
    audio = audio.apply_gain(TARGET_DBFS - audio.dBFS)
    return audio


def process_voice(voice, samples, voice_filter):
    key = voice["key"]
    gen = GENERATORS.get(voice["provider"])
    if not gen:
        print(f"  SKIP {key}: unknown provider '{voice['provider']}'")
        return
    if voice_filter and key not in voice_filter:
        return

    out_dir = PROJECT_ROOT / voice["audioPath"]
    out_dir.mkdir(parents=True, exist_ok=True)

    for st in SAMPLE_TYPES:
        out_file = out_dir / f"{st}.mp3"
        if out_file.exists():
            print(f"  SKIP {key}/{st} (exists)")
            continue
        print(f"  {key}/{st} via {voice['provider']}...", end=" ", flush=True)
        for attempt in range(3):
            try:
                raw = gen(voice, samples[st])
                normalize(raw).export(str(out_file), format="mp3", bitrate="128k")
                print(f"OK ({out_file.stat().st_size // 1024}KB)")
                time.sleep(2)
                break
            except Exception as e:
                if "429" in str(e) and attempt < 2:
                    wait = (attempt + 1) * 10
                    print(f"rate limited, waiting {wait}s...", end=" ", flush=True)
                    time.sleep(wait)
                else:
                    print(f"FAIL: {e}")
                    break


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--voices", help="Comma-separated voice keys")
    parser.add_argument("--providers", help="Comma-separated providers")
    args = parser.parse_args()

    data = json.loads(VOICES_JSON.read_text())
    all_voices = data["candidates"] + data["current"]
    if args.providers:
        provs = args.providers.split(",")
        all_voices = [v for v in all_voices if v["provider"] in provs]

    vf = args.voices.split(",") if args.voices else None
    print(f"Generating for {len(all_voices)} voices...\n")
    for v in all_voices:
        process_voice(v, data["samples"], vf)
    print("\nDone!")


if __name__ == "__main__":
    main()
