# Automated YouTube video pipeline (draft/review mode)

Script → Kokoro voiceover → Pexels visuals → ffmpeg assembly → **Google Drive
upload → email you the review link.** No auto-publish — you watch it and
upload to YouTube yourself when it's ready. Runs daily via GitHub Actions.

## Project layout
```
main.py                    # orchestrates the pipeline, nothing else
pipeline/
  config.py                # loads + validates all env vars in one place
  script_gen.py             # Claude API — writes title/description/segments
  voice.py                  # Kokoro TTS — narration audio
  visuals.py                 # Pexels — one stock image per segment
  assemble.py                # ffmpeg — Ken Burns clips + concat
  drive_delivery.py          # uploads final video to Drive (handles any length)
  discord_notify.py          # posts the Drive link to a Discord channel
```
Each module only knows its own job and takes what it needs as arguments —
`main.py` is the only place that wires them together. If you want to swap
Pexels for a different image source later, you only touch `visuals.py`.

## One-time setup

### 1. Gemini + Pexels
- **Gemini**: https://aistudio.google.com — create a free API key, no
  credit card needed. Script generation uses `gemini-2.5-flash`, well
  within the free tier's daily limits for one (or even a handful) of videos
  a day. Note: Google's free-tier terms allow your prompts/responses to be
  used to improve their models — keep that in mind if your topics are ever
  sensitive; switch to a billed project if that matters to you.
- **Pexels**: https://www.pexels.com/api/ — free, instant signup.

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
# on macOS: brew install ffmpeg espeak-ng | on Ubuntu: sudo apt install ffmpeg espeak-ng
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
- No captions burned in yet (`ffmpeg`'s `drawtext`/`subtitles` filter is the
  next natural addition).
- Kokoro's model (~327MB) downloads automatically on first use via
  Hugging Face. The workflow sets `HF_HOME` to a workspace path and caches
  it with `actions/cache`, so it only downloads once, not every run.
- Kokoro needs `espeak-ng` installed as a system package (handled in
  `publish.yml`) alongside `ffmpeg`. Testing locally, install it yourself:
  `sudo apt install espeak-ng` (Linux) or `brew install espeak-ng` (macOS).
- Once you're happy with output quality, adding an actual YouTube upload
  step back in is just one more module (`pipeline/youtube_delivery.py`) —
  the separated structure makes that a clean addition rather than a rewrite.
