# auto-tube

Multi-channel short-form video production. Each channel subclasses
`Channel`, fills in content + editing style, and shares Discord, quality
gating, voice, captions, Drive, and (optionally) trending research.

Runs **3 times a day** via GitHub Actions (5am / 1pm / 8pm Denver time).
No auto-publish to YouTube — you review the Drive link first.

Requires **Python 3.11** (chatterbox-tts).

---

## Architecture (UML)

```mermaid
classDiagram
  direction TB

  class Channel {
    +discord DiscordNotifier
    +qc QualityControl
    +workdir Path
    +run() str|None
    +prepare()* object|None
    +setup_voice()
    +generate_script(context)* dict
    +render_segments(script, context)* Path[]
    +finalize_assembly(clips, script, context)* Path
    +deliver(finalPath, script, context)* str
    +cleanup(context)
    +call_gemini_json(prompt) dict
    +generate_until_quality(generateFn) dict
    +synthesize_speech(text, outPath)
    +transcribe_words(audioPath)
    +build_segment_clip(...)
    +concat_clips(clipPaths, outPath)
    +mix_background_music(...)
    +mix_sfx_events(...)
    +upload_to_drive(folderId, filePath, filename) str
  }

  class Auto {
    +trending Trending
    +pexelsApiKey str
    +prepare() AutoContext
    +generate_script(context) dict
    +render_segments(script, context) Path[]
    +finalize_assembly(clips, script, context) Path
    +deliver(finalPath, script, context) str
    +fetch_stock_video(query, outPath)
  }

  class RankedNiche {
    +driveIntakeFolderId str
    +sfxDir Path
    +prepare() RankedNicheContext|None
    +generate_script(context) dict
    +render_segments(script, context) Path[]
    +finalize_assembly(clips, script, context) Path
    +deliver(finalPath, script, context) str
    +cleanup(context)
  }

  class DiscordNotifier {
    +webhookUrl str
    +send(message)
    +send_review(...)
    +send_research_skip(skipped)
    +send_error(error)
  }

  class QualityControl {
    +quality_threshold float
    +score(script) tuple
    +passes(totalScore) bool
  }

  class Trending {
    +research() TopicResearch
    +fetch_gemini_grounded() SourceAttempt
    +fetch_hacker_news() SourceAttempt
    +pick_topic_with_gemini(context) str
  }

  Channel <|-- Auto : inherits
  Channel <|-- RankedNiche : inherits
  Channel *-- DiscordNotifier : has
  Channel *-- QualityControl : has
  Auto *-- Trending : has
```

**Reading the diagram**

- Triangle arrow = inheritance. Auto / RankedNiche *are* Channels.
- Filled diamond = composition. Channel *owns* Discord + quality; Auto *owns* Trending.
- To add a channel: subclass `Channel`, override `prepare`, `generate_script`, `render_segments`, `finalize_assembly` (and optionally `cleanup` / `deliver`).

**One run**

```mermaid
sequenceDiagram
  participant Main as main.py
  participant Ch as Channel.run
  participant Sub as Auto or RankedNiche
  participant QC as QualityControl
  participant Disc as DiscordNotifier

  Main->>Ch: run()
  Ch->>Sub: prepare()
  Ch->>Sub: setup_voice()
  Ch->>Sub: generate_script(context)
  Sub->>QC: score / passes loop
  Ch->>Sub: render_segments(script, context)
  Ch->>Sub: finalize_assembly(clips, script, context)
  Ch->>Sub: deliver(final, script, context)
  Sub->>Disc: send_review / notify
  Ch->>Sub: cleanup(context)
```

---

## Quick start

1. Put a 5–20s voice sample at `assets/auto/voice_reference.wav` (or `.mp3`)
2. Get every key below and add them as GitHub Actions secrets
3. Push, then run **Test run** or **Daily video draft** from the Actions tab

Optional: drop royalty-free music at `assets/auto/background_music.mp3`.

For RankedNiche, also put a voice sample at `assets/rankedniche/voice_reference.wav` (or `.mp3`) and optional SFX under `assets/rankedniche/sfx/`.

---

## Secrets — how to get each one

Add these under **Repo → Settings → Secrets and variables → Actions**.

### Shared (required)

#### `GEMINI_API_KEY`
1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Create an API key
3. Paste it as the secret

Used for trending research (Google Search grounding), topic picking, and script writing.

#### `PEXELS_API_KEY`
1. Go to [Pexels API](https://www.pexels.com/api/)
2. Sign up / log in and create a key
3. Paste it as the secret

Used by Auto to download stock video clips for each script segment.

### NewNova / Auto (required)

Workflows map these GitHub secrets into `NOVA_*` env vars expected by `engine/config.py`:

| GitHub secret | Env var used at runtime |
|---------------|-------------------------|
| `DISCORD_WEBHOOK_URL` | `NOVA_DISCORD_WEBHOOK_URL` |
| `GOOGLE_CLIENT_ID` | `NOVA_GOOGLE_CLIENT_ID` |
| `GOOGLE_CLIENT_SECRET` | `NOVA_GOOGLE_CLIENT_SECRET` |
| `GOOGLE_REFRESH_TOKEN` | `NOVA_GOOGLE_REFRESH_TOKEN` |
| `DRIVE_FOLDER_ID` | `NOVA_DRIVE_FOLDER_ID` |

#### Discord webhook
1. In Discord: channel settings → **Integrations → Webhooks → New Webhook**
2. Copy the webhook URL
3. Paste it as `DISCORD_WEBHOOK_URL`

#### Google Drive (your account, not a service account)
1. In [Google Cloud Console](https://console.cloud.google.com/), create/select a project
2. Enable the **Google Drive API**
3. Create an OAuth client: **APIs & Services → Credentials → Create credentials → OAuth client ID → Desktop app**
4. Download the JSON and save it locally as `client_secret.json` in this repo folder
5. Run once on your laptop:
   ```bash
   pip install google-auth-oauthlib
   python get_drive_token.py
   ```
6. Copy the printed values into the Google secrets above
7. Create a Drive folder for reviews. The folder ID is the last part of the URL:
   `https://drive.google.com/drive/folders/THIS_PART` → secret `DRIVE_FOLDER_ID`
8. Delete local `client_secret.json` when you’re done

### RankedbyHetti / RankedNiche (optional as a whole)

If any of these are missing, that channel is skipped and Auto still runs:

- `HETTI_DISCORD_WEBHOOK_URL`
- `HETTI_GOOGLE_CLIENT_ID`
- `HETTI_GOOGLE_CLIENT_SECRET`
- `HETTI_GOOGLE_REFRESH_TOKEN`
- `HETTI_DRIVE_INTAKE_FOLDER_ID`
- `HETTI_DRIVE_OUTPUT_FOLDER_ID`

### Optional (research / fallbacks)

Missing optional secrets just skip that source (Discord will say so). The run only fails if **every** research source fails and you didn’t set a topic override.

#### `HF_TOKEN`
Fallback when Gemini is down for script writing or topic picking.

#### `YOUTUBE_API_KEY`
Used for the “YouTube most popular” research source.

#### `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`
Used for authenticated Reddit trending (anonymous `.json` is often blocked from GitHub Actions).

---

## Local run

```bash
# Python 3.11 + ffmpeg required
pip install -r requirements.txt

export GEMINI_API_KEY=...
export PEXELS_API_KEY=...
export NOVA_DISCORD_WEBHOOK_URL=...
export NOVA_GOOGLE_CLIENT_ID=...
export NOVA_GOOGLE_CLIENT_SECRET=...
export NOVA_GOOGLE_REFRESH_TOKEN=...
export NOVA_DRIVE_FOLDER_ID=...
# optional:
# export HF_TOKEN=...
# export YOUTUBE_API_KEY=...
# export REDDIT_CLIENT_ID=...
# export REDDIT_CLIENT_SECRET=...
# export VIDEO_TOPIC="optional fixed topic"
# export HETTI_*=...

python main.py
```

Auto output lands under `build/auto/` before Drive upload.

Leave `VIDEO_TOPIC` unset to research trending automatically.

---

## Layout

| Path | Role |
|------|------|
| `main.py` | Builds Auto (+ RankedNiche if configured) and calls `.run()` |
| `engine/Channel.py` | Template method + shared voice / assemble / Drive / Discord / QC |
| `engine/auto.py` | Autonomous research + Pexels + BGM channel |
| `engine/RankedNiche.py` | Drive intake countdown + SFX channel |
| `engine/Trending.py` | Multi-source topic research |
| `engine/QualityControl.py` | Script quality gate |
| `engine/DiscordNotify.py` | Discord webhook notifier |
| `engine/assemble.py` | ffmpeg segments, captions, BGM/SFX mix |
| `engine/voice.py` | Chatterbox-Turbo voice clone |
| `engine/drive.py` | Google Drive helpers |
| `engine/config.py` | Shared / Nova / Hetti env loading |
| `get_drive_token.py` | One-time Drive OAuth helper |
