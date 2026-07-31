"""All environment/config reading lives here, split into three groups:

- SharedConfig: infra used by both pipelines (Gemini/Pexels/HF keys).
  Always required -- NewNova can't run without these.
- NovaConfig: NewNova's own Drive account + Discord webhook. Always
  required -- NewNova runs on every scheduled invocation.
- HettiConfig: RankedbyHetti's own (separate) Drive account + Discord
  webhook. Optional AS A WHOLE -- if any piece is missing, RankedbyHetti
  is treated as "not set up yet" and that stage is skipped for this run
  rather than failing it, so NewNova keeps working standalone until
  Hetti's secrets are actually filled in."""

import os
from dataclasses import dataclass


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class SharedConfig:
    gemini_api_key: str
    hf_token: str | None
    pexels_api_key: str
    youtube_api_key: str | None
    reddit_client_id: str | None
    reddit_client_secret: str | None
    video_topic: str | None


@dataclass(frozen=True)
class NovaConfig:
    discord_webhook_url: str
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    drive_folder_id: str


@dataclass(frozen=True)
class HettiConfig:
    discord_webhook_url: str
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    drive_intake_folder_id: str
    drive_output_folder_id: str


def load_shared_config() -> SharedConfig:
    return SharedConfig(
        gemini_api_key=require("GEMINI_API_KEY"),
        hf_token=os.environ.get("HF_TOKEN") or None,
        pexels_api_key=require("PEXELS_API_KEY"),
        youtube_api_key=os.environ.get("YOUTUBE_API_KEY") or None,
        reddit_client_id=os.environ.get("REDDIT_CLIENT_ID") or None,
        reddit_client_secret=os.environ.get("REDDIT_CLIENT_SECRET") or None,
        video_topic=os.environ.get("VIDEO_TOPIC") or None,
    )


def load_nova_config() -> NovaConfig:
    return NovaConfig(
        discord_webhook_url=require("NOVA_DISCORD_WEBHOOK_URL"),
        google_client_id=require("NOVA_GOOGLE_CLIENT_ID"),
        google_client_secret=require("NOVA_GOOGLE_CLIENT_SECRET"),
        google_refresh_token=require("NOVA_GOOGLE_REFRESH_TOKEN"),
        drive_folder_id=require("NOVA_DRIVE_FOLDER_ID"),
    )


HETTI_VARS = [
    "HETTI_DISCORD_WEBHOOK_URL",
    "HETTI_GOOGLE_CLIENT_ID",
    "HETTI_GOOGLE_CLIENT_SECRET",
    "HETTI_GOOGLE_REFRESH_TOKEN",
    "HETTI_DRIVE_INTAKE_FOLDER_ID",
    "HETTI_DRIVE_OUTPUT_FOLDER_ID",
]


def load_hetti_config() -> HettiConfig | None:
    """Returns None (not an error) when Hetti's secrets aren't fully set --
    lets main.py skip straight to NewNova on days/setups where Hetti isn't
    wired up yet, per the "if active, else just Nova" scheduling model."""
    values = {name: os.environ.get(name) for name in HETTI_VARS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        print(f"RankedbyHetti not fully configured (missing {missing}) -- skipping that stage.")
        return None

    return HettiConfig(
        discord_webhook_url=values["HETTI_DISCORD_WEBHOOK_URL"],
        google_client_id=values["HETTI_GOOGLE_CLIENT_ID"],
        google_client_secret=values["HETTI_GOOGLE_CLIENT_SECRET"],
        google_refresh_token=values["HETTI_GOOGLE_REFRESH_TOKEN"],
        drive_intake_folder_id=values["HETTI_DRIVE_INTAKE_FOLDER_ID"],
        drive_output_folder_id=values["HETTI_DRIVE_OUTPUT_FOLDER_ID"],
    )