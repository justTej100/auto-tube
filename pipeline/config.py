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
        self.pexels_api_key = _require("PEXELS_API_KEY")

        self.google_service_account_json_b64 = _require("GOOGLE_SERVICE_ACCOUNT_JSON")
        self.drive_folder_id = _require("DRIVE_FOLDER_ID")

        self.email_address = _require("EMAIL_ADDRESS")
        self.email_app_password = _require("EMAIL_APP_PASSWORD")
        self.email_to = os.environ.get("EMAIL_TO", self.email_address)

        self.video_topic = os.environ.get("VIDEO_TOPIC", "a surprising fact about the deep ocean")
