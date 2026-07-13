# Automated YouTube pipeline

Script → Piper voiceover → Pexels visuals → ffmpeg assembly → YouTube upload,
run daily via GitHub Actions. Free stack except a couple cents of Claude API
usage per video.

## One-time setup

### 1. Get your API keys
- **Anthropic**: https://console.anthropic.com — create a key, add a few
  dollars of prepaid credit (usage will be tiny, well under $1/month for
  script generation alone).
- **Pexels**: https://www.pexels.com/api/ — free, instant signup.
- **YouTube**: in [Google Cloud Console](https://console.cloud.google.com),
  create a project, enable "YouTube Data API v3", then create an OAuth
  Client ID of type **Desktop app** and download it as `client_secret.json`.

### 2. Generate a YouTube refresh token (run locally, once)
```bash
pip install google-auth-oauthlib
python get_youtube_token.py
```
This opens a browser for you to log into the Google account that owns your
YouTube channel. It prints three values — copy them for the next step.

### 3. Add GitHub repo secrets
Repo → Settings → Secrets and variables → Actions → New repository secret.
Add all five:
- `ANTHROPIC_API_KEY`
- `PEXELS_API_KEY`
- `YOUTUBE_CLIENT_ID`
- `YOUTUBE_CLIENT_SECRET`
- `YOUTUBE_REFRESH_TOKEN`

### 4. Push this repo to GitHub
The workflow in `.github/workflows/publish.yml` will then run daily at the
scheduled time, and you can also trigger it manually from the Actions tab
(`workflow_dispatch`).

## Testing locally before you trust the cron job
```bash
pip install -r requirements.txt
# on macOS: brew install ffmpeg | on Ubuntu: sudo apt install ffmpeg
export ANTHROPIC_API_KEY=...
export PEXELS_API_KEY=...
export YOUTUBE_CLIENT_ID=...
export YOUTUBE_CLIENT_SECRET=...
export YOUTUBE_REFRESH_TOKEN=...
python pipeline.py
```
Output video lands at `build/final.mp4` before upload — check it before
trusting the automated run.

## Notes / things you'll likely want to change
- Videos upload as **private** by default (see `pipeline.py`,
  `upload_to_youtube`). Flip to `"public"` or add a `publishAt` timestamp
  once you trust the output.
- `VIDEO_TOPIC` is hardcoded in the workflow env — swap this for logic that
  picks from a rotating list, a CSV, or lets Claude pick a fresh topic each
  run.
- No captions/subtitles burned in yet — `ffmpeg`'s `drawtext` or `subtitles`
  filter is the next thing to add if you want them.
- Piper's voice model (~60MB) downloads fresh every run since Actions
  runners are ephemeral. Fine for daily use; add `actions/cache` if you want
  to skip the re-download.