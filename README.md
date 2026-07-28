# auto-tube

Researches a trending topic → writes a script → clones your voice → pulls
Pexels video clips → assembles with ffmpeg → uploads to Google Drive →
pings Discord for review.

Runs **3 times a day** via GitHub Actions (5am / 1pm / 8pm Denver time).
No auto-publish to YouTube — you review the Drive link first.

Requires **Python 3.11** (chatterbox-tts).

---

## Quick start

1. Put a 5–20s voice sample at `assets/voice_reference.wav` (or `.mp3`)
2. Get every key below and add them as GitHub Actions secrets
3. Push, then run **Test run** or **Daily video draft** from the Actions tab

Optional: drop royalty-free music at `assets/background_music.mp3`.

---

## Secrets — how to get each one

Add these under **Repo → Settings → Secrets and variables → Actions**.

### Required

#### `GEMINI_API_KEY`
1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Create an API key
3. Paste it as the secret

Used for trending research (Google Search grounding), topic picking, and script writing.

#### `PEXELS_API_KEY`
1. Go to [Pexels API](https://www.pexels.com/api/)
2. Sign up / log in and create a key
3. Paste it as the secret

Used to download stock video clips for each script segment.

#### `DISCORD_WEBHOOK_URL`
1. In Discord: channel settings → **Integrations → Webhooks → New Webhook**
2. Copy the webhook URL
3. Paste it as the secret

Used for “video ready” links and research-skip warnings. Treat it like a password.

#### `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REFRESH_TOKEN` / `DRIVE_FOLDER_ID`

Drive uploads use **your** Google account (not a service account).

1. In [Google Cloud Console](https://console.cloud.google.com/), create/select a project
2. Enable the **Google Drive API**
3. Create an OAuth client: **APIs & Services → Credentials → Create credentials → OAuth client ID → Desktop app**
4. Download the JSON and save it locally as `client_secret.json` in this repo folder
5. Run once on your laptop:
   ```bash
   pip install google-auth-oauthlib
   python get_drive_token.py
   ```
6. Copy the printed `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN` into secrets
7. Create a Drive folder for reviews. The folder ID is the last part of the URL:
   `https://drive.google.com/drive/folders/THIS_PART` → secret `DRIVE_FOLDER_ID`
8. Delete local `client_secret.json` when you’re done

If the refresh token ever expires/revokes, re-run `get_drive_token.py` and update the secrets.

---

### Optional (research / fallbacks)

Missing optional secrets just skip that source (Discord will say so). The run only fails if **every** research source fails and you didn’t set a topic override.

#### `HF_TOKEN`
1. Go to [Hugging Face tokens](https://huggingface.co/settings/tokens)
2. Create a token with **Read** access
3. Paste it as the secret

Fallback when Gemini is down for script writing or topic picking.

#### `YOUTUBE_API_KEY`
1. In [Google Cloud Console](https://console.cloud.google.com/), same project is fine
2. Enable **YouTube Data API v3**
3. **Credentials → Create credentials → API key**
4. Paste it as the secret

Used for the “YouTube most popular” research source.

#### `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`
1. Log into Reddit → [prefs/apps](https://www.reddit.com/prefs/apps)
2. Click **create another app…**
3. Type: **script**, name/description anything, redirect uri `http://localhost:8080`
4. After create: the string under the app name is the **client id**; the **secret** is labeled secret
5. Paste both as secrets

Used for authenticated Reddit trending (anonymous `.json` is often blocked from GitHub Actions).

---

## Local run

```bash
# Python 3.11 + ffmpeg required
pip install -r requirements.txt

export GEMINI_API_KEY=...
export PEXELS_API_KEY=...
export GOOGLE_CLIENT_ID=...
export GOOGLE_CLIENT_SECRET=...
export GOOGLE_REFRESH_TOKEN=...
export DRIVE_FOLDER_ID=...
export DISCORD_WEBHOOK_URL=...
# optional:
# export HF_TOKEN=...
# export YOUTUBE_API_KEY=...
# export REDDIT_CLIENT_ID=...
# export REDDIT_CLIENT_SECRET=...
# export VIDEO_TOPIC="optional fixed topic"

python main.py
```

Output lands at `build/final.mp4` before Drive upload.

Leave `VIDEO_TOPIC` unset to research trending automatically.

---

## Layout

| Path | Role |
|------|------|
| `main.py` | Wires the pipeline |
| `pipeline/trending.py` | Multi-source topic research |
| `pipeline/script_gen.py` | Gemini (+ HF fallback) script |
| `pipeline/voice.py` | Chatterbox-Turbo voice clone |
| `pipeline/visuals.py` | Pexels video download |
| `pipeline/assemble.py` | ffmpeg segments + captions |
| `pipeline/drive_delivery.py` | Drive upload |
| `pipeline/discord_notify.py` | Discord webhooks |
| `get_drive_token.py` | One-time Drive OAuth helper |
