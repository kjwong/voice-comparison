# Voice Comparison

Static page for A/B comparing TTS voices across providers (ElevenLabs, Azure, Google, Cartesia, Inworld).

Live: <https://kjwong.github.io/voice-comparison/>

## How it works

`voices.json` is the source of truth. The page renders cards in five sections, controlled by per-voice flags:

| Section | Flag | Purpose |
|---|---|---|
| Latest additions | `latest: true` | Newly added voices currently being evaluated |
| Will replace | `willReplace: true` | Voices in production we're moving off of |
| Considered last time | `consideredLastTime: true` | Voices evaluated in a prior round, kept for reference |
| New Voices | _(none)_ | Default bucket for candidates |
| Current Production Voices | _(in `current[]` array)_ | What we ship today |

A voice belongs to exactly one section — flags are checked in the order above. Toggling a flag in `voices.json` is the only thing needed to move a card; no code change.

## Evaluation rounds (tabs)

Every candidate also has a `rounds: ["YYYY-MM-DD"]` array indicating which evaluation sessions it belongs to. The page derives a tab bar from these dates, sorted newest-first, and defaults to the most recent round.

- A voice can belong to multiple rounds (e.g., the Inworld voices are candidates on `2026-03-25` *and* "Will replace" on `2026-04-30`).
- Section flags (`latest`, `willReplace`, `consideredLastTime`) are interpreted only on the most recent tab. Older tabs collapse all their voices into the "New Voices" section — those flags reflect today's framing, not historical context.
- Production voices in `current[]` are tab-independent; they show on every tab.

To start a new evaluation round, pick a date and add it to the `rounds` array of voices you're including. A new tab appears automatically.

## Adding a new voice

1. Append a new object to `candidates[]` in `voices.json`. Use the same shape as existing entries — see `audioPath` matches `audio/<provider>/<key>/`.
2. Generate audio samples:
   ```bash
   source venv/bin/activate && source .env
   python scripts/generate_samples.py --voices <key>
   ```
3. Commit the JSON change + the new `audio/<provider>/<key>/` directory and push.

The script skips voices whose MP3s already exist, so re-running is safe.

## Sample texts and speed variants

Defined in `voices.json` under `samples`. The script generates five files per voice:

- `sample1.mp3`, `sample2.mp3`, `sample3.mp3` (one per text)
- `sample1_slow.mp3` (0.75×), `sample1_fast.mp3` (1.2×)

Speed control quirks per provider:

| Provider | Mechanism | Notes |
|---|---|---|
| ElevenLabs | `voice_settings.speed` | Clamped to 0.7–1.2 |
| Azure | SSML `<prosody rate>` | DragonHDLatestNeural honors it despite docs claiming otherwise |
| Google | `audio_config.speaking_rate` | Native API parameter |
| Inworld | `audioConfig.speakingRate` | Clamped to 0.5–1.5 |
| Cartesia | `generation_config.speed` | Clamped to 0.6–1.5 |

## Local development

```bash
python3 -m http.server 8000
```

Open <http://localhost:8000/>. No build step — pure static HTML/CSS/JS.

## Generating audio (one-time setup)

Sample generation requires API keys for each provider. Create `.env` in the project root:

```bash
ELEVENLABS_API_KEY=...
AZURE_TTS_API_KEY=...
GOOGLE_APPLICATION_CREDENTIALS_RAW=...   # base64-encoded service account JSON
INWORLD_API_KEY=...                      # base64 "username:password"
CARTESIA_API_KEY=...
```

Python deps:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r scripts/requirements.txt
```

The `.env` file is gitignored — keys never leave your machine. The deployed site is static and makes no API calls; pre-generated MP3s are served from `audio/`.

## Deployment

GitHub Pages serves directly from `main`. To deploy: `git push origin main`. Pages typically rebuilds within 30–90 seconds.
