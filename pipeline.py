"""
Automated YouTube pipeline.

Stages:
  1. generate_script   -> Claude API writes title/description/narration segments
  2. synthesize_speech  -> Piper TTS (free, commercial-safe, runs locally)
  3. fetch_images       -> Pexels API pulls one stock image per narration segment
  4. build_video        -> ffmpeg builds a Ken Burns clip per segment, then concats
  5. upload_to_youtube  -> YouTube Data API v3 resumable upload

Run with: python pipeline.py
Required env vars are listed in README.md.
"""

import json
import os
import subprocess
import wave
from pathlib import Path

import anthropic
import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

WORKDIR = Path("build")
VOICES_DIR = Path("voices")
PIPER_MODEL = VOICES_DIR / "en_US-lessac-medium.onnx"
PIPER_MODEL_JSON = VOICES_DIR / "en_US-lessac-medium.onnx.json"
PIPER_VOICE_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "en/en_US/lessac/medium/en_US-lessac-medium.onnx"
)
PIPER_VOICE_JSON_URL = PIPER_VOICE_URL + ".json"


# ---------- 1. Script generation ----------

def generate_script(topic: str) -> dict:
    """Ask Claude for a title, description, and a list of short narration
    segments, each paired with an image search query."""
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    prompt = f"""Write a short YouTube video script about: {topic}

Return ONLY valid JSON, no markdown fences, no preamble, in this exact shape:
{{
  "title": "...",
  "description": "...",
  "segments": [
    {{"narration": "one or two sentences", "image_query": "2-4 word stock photo search"}},
    ... (6 to 10 segments total)
  ]
}}"""

    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    return json.loads(text)


# ---------- 2. Voiceover (Piper TTS) ----------

def ensure_piper_voice():
    VOICES_DIR.mkdir(exist_ok=True)
    if not PIPER_MODEL.exists():
        print("Downloading Piper voice model...")
        for url, dest in [(PIPER_VOICE_URL, PIPER_MODEL), (PIPER_VOICE_JSON_URL, PIPER_MODEL_JSON)]:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            dest.write_bytes(r.content)


def synthesize_speech(text: str, out_path: Path):
    subprocess.run(
        ["piper", "--model", str(PIPER_MODEL), "--output_file", str(out_path)],
        input=text,
        text=True,
        check=True,
    )


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as f:
        return f.getnframes() / f.getframerate()


# ---------- 3. Visuals (Pexels) ----------

def fetch_image(query: str, out_path: Path):
    headers = {"Authorization": os.environ["PEXELS_API_KEY"]}
    r = requests.get(
        "https://api.pexels.com/v1/search",
        params={"query": query, "per_page": 1, "orientation": "landscape"},
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    photos = r.json().get("photos")
    if not photos:
        raise RuntimeError(f"No Pexels results for query: {query}")
    img_url = photos[0]["src"]["large2x"]
    img = requests.get(img_url, timeout=60)
    img.raise_for_status()
    out_path.write_bytes(img.content)


# ---------- 4. Assembly (ffmpeg) ----------

def build_segment_clip(image_path: Path, audio_path: Path, out_path: Path):
    duration = wav_duration_seconds(audio_path)
    frames = int(duration * 25)  # 25 fps
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(image_path),
            "-i", str(audio_path),
            "-t", str(duration),
            "-vf",
            f"scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080,"
            f"zoompan=z='min(zoom+0.0006,1.2)':d={frames}:s=1920x1080:fps=25",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(out_path),
        ],
        check=True,
    )


def concat_clips(clip_paths: list[Path], out_path: Path):
    list_file = WORKDIR / "concat_list.txt"
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in clip_paths))
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)],
        check=True,
    )


# ---------- 5. Upload ----------

def upload_to_youtube(video_path: Path, title: str, description: str):
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    creds.refresh(Request())

    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {"title": title, "description": description, "categoryId": "22"},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")
    print(f"Uploaded. Video ID: {response['id']}")
    return response["id"]


# ---------- Orchestration ----------

def main():
    topic = os.environ.get("VIDEO_TOPIC", "a surprising fact about the deep ocean")

    WORKDIR.mkdir(exist_ok=True)
    ensure_piper_voice()

    print(f"Generating script for topic: {topic}")
    script = generate_script(topic)

    clip_paths = []
    for i, seg in enumerate(script["segments"]):
        print(f"Segment {i}: {seg['narration'][:60]}...")
        audio_path = WORKDIR / f"seg_{i}.wav"
        image_path = WORKDIR / f"seg_{i}.jpg"
        clip_path = WORKDIR / f"seg_{i}.mp4"

        synthesize_speech(seg["narration"], audio_path)
        fetch_image(seg["image_query"], image_path)
        build_segment_clip(image_path, audio_path, clip_path)
        clip_paths.append(clip_path)

    final_path = WORKDIR / "final.mp4"
    concat_clips(clip_paths, final_path)
    print(f"Video assembled: {final_path}")

    upload_to_youtube(final_path, script["title"], script["description"])


if __name__ == "__main__":
    main()
