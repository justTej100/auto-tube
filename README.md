# Automated YouTube video pipeline (draft/review mode)

Script → Chatterbox-Turbo voice clone → Pexels visuals → ffmpeg assembly → **Google Drive
upload → email you the review link.** No auto-publish — you watch it and
upload to YouTube yourself when it's ready. Runs daily via GitHub Actions.

## Project layout
```
main.py                    # orchestrates the pipeline, nothing else
pipeline/
  config.py                # loads + validates all env vars in one place
  script_gen.py             # Claude API — writes title/description/segments
  voice.py                  # Chatterbox-Turbo — voice-cloned narration (CPU, self-hosted, free)
  visuals.py                 # Pexels — one stock image per segment
  assemble.py                # ffmpeg — Ken Burns clips, burned-in captions, concat, optional music
  drive_delivery.py          # uploads final video to Drive (handles any length)
  discord_notify.py          # posts the Drive link to a Discord channel
```
Each module only knows its own job and takes what it needs as arguments —
`main.py` is the only place that wires them together. If you want to swap
Pexels for a different image source later, you only touch `visuals.py`.

## One-time setup

### 0. Voice reference clip
Voice cloning needs a sample of the target voice to clone. Record 5-20
seconds of clean audio (one speaker, minimal background noise, your own
voice with your own consent) and save it as `assets/voice_reference.wav`
**or** `assets/voice_reference.mp3` in this repo before running anything
— mp3 gets auto-converted to wav via ffmpeg, either format works fine.

### 1. Gemini + Pexels (+ optional Hugging Face fallback)
- **Gemini**: https://aistudio.google.com — create a free API key, no
  credit card needed. Script generation uses `gemini-3-flash-preview`,
  well within the free tier's daily limits for one (or even a handful) of
  videos a day. Note: Google's free-tier terms allow your prompts/responses
  to be used to improve their models — keep that in mind if your topics
  are ever sensitive; switch to a billed project if that matters to you.
- **Pexels**: https://www.pexels.com/api/ — free, instant signup.
- **(Optional) Hugging Face** — only needed for automatic fallback when
  Gemini has an outage or hits a "high demand" 503. If Gemini fails after
  3 retries, the pipeline automatically retries with a free Hugging
  Face-hosted model instead of just failing the run. Get a free token at
  https://huggingface.co/settings/tokens (Read access is enough) and add
  it as `HF_TOKEN`. Skip this if you're fine with a run occasionally
  failing during a Gemini outage — without this secret set, behavior is
  unchanged, Gemini errors just raise as before.

### 2. Google OAuth (for Drive uploads)
Service accounts can't upload to a personal Drive — they have no storage
quota of their own on consumer Google accounts (that path only works on
paid Workspace accounts with Shared Drives). So uploads authenticate as
your actual Google account instead, via a one-time login.

1. In [Google Cloud Console](https://console.cloud.google.com), create a
   project (or reuse one), enable the **Google Drive API**, then create an
   OAuth 2.0 Client ID of type **Desktop app**. Download it as
   `client_secret.json` into this project folder.
2. Run this once, locally (not in GitHub Actions):
   ```bash
   pip install google-auth-oauthlib
   python get_drive_token.py
   ```
   This opens a browser for you to log into the Google account you want
   videos uploaded to. It prints three values — copy them for the next
   step.
3. In Google Drive, create (or pick) a folder for review videos and grab
   its ID from the URL (`drive.google.com/drive/folders/THIS_PART`) — no
   sharing step needed this time, since you're uploading as yourself now,
   not as a separate service account.
4. Once all three values are safely saved as GitHub secrets, **delete your
   local `client_secret.json` and any venv you made to run this script.**
   Neither is needed again — the credentials now live in GitHub Secrets,
   which is what the pipeline actually reads from:
   ```bash
   rm -rf venv client_secret.json get_drive_token.py
   ```
   (If you ever need to redo this — e.g. the refresh token gets revoked —
   you can always re-download a fresh client_secret.json from Google Cloud
   Console → Google Auth Platform → Clients → your Desktop app client.)

### 3. Discord webhook (for the notification)
No login, no bot, no OAuth. In Discord: open the channel you want notified →
**Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL**.
That URL is the entire credential — treat it like a password (anyone who
has it can post into that channel), so it goes straight into GitHub
Secrets, never hardcoded.

### 4. Add GitHub repo secrets
Repo → Settings → Secrets and variables → Actions → New repository secret:
- `GEMINI_API_KEY`
- `HF_TOKEN` (optional — enables the Gemini-outage fallback described above)
- `PEXELS_API_KEY`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`
- `DRIVE_FOLDER_ID`
- `DISCORD_WEBHOOK_URL`

### 5. Push and trigger it once manually
Push this repo to GitHub, then go to the Actions tab and run the workflow
by hand (`workflow_dispatch`) before trusting the daily cron.

## Testing locally
```bash
pip install -r requirements.txt
# on macOS: brew install ffmpeg | on Ubuntu: sudo apt install ffmpeg
# Note: chatterbox-tts requires Python 3.11 specifically.
# Add your voice sample at assets/voice_reference.wav before running.
export GEMINI_API_KEY=...
export PEXELS_API_KEY=...
export GOOGLE_CLIENT_ID=...
export GOOGLE_CLIENT_SECRET=...
export GOOGLE_REFRESH_TOKEN=...
export DRIVE_FOLDER_ID=...
export DISCORD_WEBHOOK_URL=...
python main.py
```
Assembled video lands at `build/final.mp4` before it's uploaded — worth
checking on the first couple of runs.

## Things you'll likely want to change next
- `VIDEO_TOPIC` is hardcoded in the workflow env — swap for logic that
  rotates topics or lets Claude pick something new each run.
- Captions are burned into every segment via `drawtext`, wrapped to a
  readable width automatically.
- **Optional background music**: drop a royalty-free track (from wherever
  you like — YouTube Audio Library, Pixabay Music, etc.) at
  `assets/background_music.mp3`. It'll be mixed in at low volume under the
  narration automatically. No file there = no music, silently skipped.
- Chatterbox-Turbo's model downloads automatically on first use via
  Hugging Face. The workflow sets `HF_HOME` to a workspace path and caches
  it with `actions/cache`, so it only downloads once, not every run.
- **Runs on CPU, self-hosted, genuinely $0** — but no confirmed benchmark
  exists for this model's CPU speed specifically, so **test via `test.yml`
  before trusting timing in the daily matrix run.** If it's too slow for
  your video length, the two fallback options are paying for a hosted GPU
  API (fal.ai has Chatterbox-Turbo at ~$0.025/1,000 characters) or paying
  for ElevenLabs directly.
- Requires **Python 3.11 specifically** — both workflow files are pinned
  to it; don't bump to a newer Python version without checking
  chatterbox-tts's compatibility first.
- Once you're happy with output quality, adding an actual YouTube upload
  step back in is just one more module (`pipeline/youtube_delivery.py`) —
  the separated structure makes that a clean addition rather than a rewrite.
