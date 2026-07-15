# Automated YouTube video pipeline (draft/review mode)

Script → Piper voiceover → Pexels visuals → ffmpeg assembly → **Google Drive
upload → email you the review link.** No auto-publish — you watch it and
upload to YouTube yourself when it's ready. Runs daily via GitHub Actions.

## Project layout
```
main.py                    # orchestrates the pipeline, nothing else
pipeline/
  config.py                # loads + validates all env vars in one place
  script_gen.py             # Claude API — writes title/description/segments
  voice.py                  # Piper TTS — narration audio
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

### 2. Google service account (for Drive uploads)
This replaces the old YouTube OAuth flow entirely — no browser login step,
no refresh tokens to babysit.

1. In [Google Cloud Console](https://console.cloud.google.com), create a
   project (or reuse one), then enable the **Google Drive API**.
2. Go to IAM & Admin → Service Accounts → Create Service Account. Give it
   any name.
3. Open the new service account → Keys → Add Key → Create new key → JSON.
   This downloads a `service-account.json` file — **keep it private, it's a
   credential.**
4. Base64-encode it (needed to store safely as a GitHub secret):
   ```bash
   base64 -w0 service-account.json      # Linux
   base64 -i service-account.json       # macOS
   ```
   Copy the output — this whole string is your `GOOGLE_SERVICE_ACCOUNT_JSON`
   secret.
5. In Google Drive, create (or pick) a folder for review videos, open its
   sharing settings, and **share it with the service account's email**
   (looks like `something@your-project.iam.gserviceaccount.com`, found in
   the JSON file or the Cloud Console) as **Editor**.
6. Grab that folder's ID from its URL
   (`drive.google.com/drive/folders/THIS_PART`) — that's your
   `DRIVE_FOLDER_ID`.

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
- `GOOGLE_SERVICE_ACCOUNT_JSON` (the base64 string from step 2.4)
- `DRIVE_FOLDER_ID`
- `DISCORD_WEBHOOK_URL`

### 5. Push and trigger it once manually
Push this repo to GitHub, then go to the Actions tab and run the workflow
by hand (`workflow_dispatch`) before trusting the daily cron.

## Testing locally
```bash
pip install -r requirements.txt
# on macOS: brew install ffmpeg | on Ubuntu: sudo apt install ffmpeg
export GEMINI_API_KEY=...
export PEXELS_API_KEY=...
export GOOGLE_SERVICE_ACCOUNT_JSON=...
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
- Piper's voice model re-downloads every run since Actions runners are
  ephemeral — add `actions/cache` if you want to skip that.
- Once you're happy with output quality, adding an actual YouTube upload
  step back in is just one more module (`pipeline/youtube_delivery.py`) —
  the separated structure makes that a clean addition rather than a rewrite.
