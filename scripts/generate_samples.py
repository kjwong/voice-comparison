#!/usr/bin/env python3
"""Generate TTS audio samples for all voices in voices.json.

Usage:
    python scripts/generate_samples.py                          # All voices
    python scripts/generate_samples.py --voices rachel,onyx     # Specific voices
    python scripts/generate_samples.py --providers elevenlabs   # Specific provider

Requires .env in project root:
    ELEVENLABS_API_KEY=...
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
TARGET_DBFS = -20.0

# sample_key -> (filename, speed_factor or None for default)
SAMPLES_TO_GENERATE = {
    "sample1": (None,),
    "sample2": (None,),
    "sample3": (None,),
    "sample1_slow": ("sample1", 0.75),
    "sample1_fast": ("sample1", 1.2),
}


ELEVENLABS_DEFAULT_SETTINGS = {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
}


def generate_elevenlabs(voice_id, model, text, speed=None):
    payload = {"text": text, "model_id": model}
    if speed is not None:
        settings = dict(ELEVENLABS_DEFAULT_SETTINGS)
        settings["speed"] = max(0.7, min(1.2, speed))
        payload["voice_settings"] = settings
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128",
        json=payload,
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"],
                 "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


def generate_azure(voice_id, text, speed=None):
    region = "southeastasia"
    safe = (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("'", "&apos;").replace('"', "&quot;"))

    if speed is not None:
        # Convert speed factor to percentage offset: 0.75 -> "-25%", 1.2 -> "+20%"
        pct = round((speed - 1.0) * 100)
        prefix = "+" if pct >= 0 else ""
        inner = f'<prosody rate="{prefix}{pct}.00%">{safe}</prosody>'
    else:
        inner = safe

    ssml = (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="en-US"><voice name="{voice_id}">{inner}</voice></speak>'
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


def generate_inworld(voice_id, model, text, speed=None):
    payload = {
        "text": text,
        "voiceId": voice_id,
        "modelId": model,
        "audioConfig": {
            "audioEncoding": "MP3",
            "sampleRateHertz": 44100,
            "bitRate": 128000,
        },
    }
    if speed is not None:
        payload["audioConfig"]["speakingRate"] = max(0.5, min(1.5, speed))
    resp = requests.post(
        "https://api.inworld.ai/tts/v1/voice",
        json=payload,
        headers={"Authorization": f"Basic {os.environ['INWORLD_API_KEY']}",
                 "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return base64.b64decode(data["audio"])


_google_client = None

def _get_google_tts_client():
    global _google_client
    if _google_client is None:
        from google.cloud import texttospeech
        from google.oauth2 import service_account
        credentials = service_account.Credentials.from_service_account_info(
            json.loads(base64.b64decode(os.environ["GOOGLE_APPLICATION_CREDENTIALS_RAW"]).decode("utf-8"))
        )
        _google_client = texttospeech.TextToSpeechClient(credentials=credentials)
    return _google_client


def generate_google(voice_id, text, speed=None):
    from google.cloud import texttospeech
    client = _get_google_tts_client()
    parts = voice_id.split("-")
    lang = f"{parts[0]}-{parts[1]}"
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=speed if speed is not None else 1.0,
    )
    response = client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(language_code=lang, name=voice_id),
        audio_config=audio_config,
    )
    return response.audio_content


GENERATORS = {
    "elevenlabs": lambda v, t, s: generate_elevenlabs(v["voiceId"], v["model"], t, s),
    "azure": lambda v, t, s: generate_azure(v["voiceId"], t, s),
    "google": lambda v, t, s: generate_google(v["voiceId"], t, s),
    "inworld": lambda v, t, s: generate_inworld(v["voiceId"], v["model"], t, s),
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

    for file_key, config in SAMPLES_TO_GENERATE.items():
        out_file = out_dir / f"{file_key}.mp3"
        if out_file.exists():
            print(f"  SKIP {key}/{file_key} (exists)")
            continue

        if len(config) == 1:
            # Normal sample: use file_key as sample key, no speed
            sample_key = file_key
            speed = None
        else:
            # Speed variant: config is (sample_key, speed_factor)
            sample_key, speed = config

        text = samples[sample_key]
        speed_label = f" @{speed}x" if speed else ""
        print(f"  {key}/{file_key}{speed_label} via {voice['provider']}...", end=" ", flush=True)

        for attempt in range(3):
            try:
                raw = gen(voice, text, speed)
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
