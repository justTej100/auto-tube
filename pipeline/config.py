"""All environment/config reading lives here. Every other module imports
from this file instead of touching os.environ directly — one place to see
everything the pipeline depends on, and one place that fails loudly if
something's missing instead of dying halfway through a run."""

import os


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


class Config:
    def __init__(self):
        self.gemini_api_key = _require("GEMINI_API_KEY")
        self.hf_token = os.environ.get("HF_TOKEN")  # optional -- enables the fallback
        self.pexels_api_key = _require("PEXELS_API_KEY")

        self.google_client_id = _require("GOOGLE_CLIENT_ID")
        self.google_client_secret = _require("GOOGLE_CLIENT_SECRET")
        self.google_refresh_token = _require("GOOGLE_REFRESH_TOKEN")
        self.drive_folder_id = _require("DRIVE_FOLDER_ID")

        self.discord_webhook_url = _require("DISCORD_WEBHOOK_URL")

        self.video_topic = os.environ.get("VIDEO_TOPIC", "a surprising fact about the deep ocean")
        self.voice_name = os.environ.get("VOICE_NAME", "af_heart")
        self.voice_speed = float(os.environ.get("VOICE_SPEED", "1.0"))
