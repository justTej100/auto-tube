"""Top-level entry point: checks RankedNiche's intake folder first (if
that channel is configured), then always runs Auto. RankedNiche failures
are logged and reported to Discord but never block Auto -- Auto is the
channel that has to keep working every single run without anyone touching
it, per its design goal."""

import os

from engine.Auto import Auto
from engine.RankedNiche import RankedNiche

RANKEDNICHE_VARS = [
    "RANKEDNICHE_DISCORD_WEBHOOK_URL",
    "RANKEDNICHE_GOOGLE_CLIENT_ID",
    "RANKEDNICHE_GOOGLE_CLIENT_SECRET",
    "RANKEDNICHE_GOOGLE_REFRESH_TOKEN",
    "RANKEDNICHE_DRIVE_INTAKE_FOLDER_ID",
    "RANKEDNICHE_DRIVE_OUTPUT_FOLDER_ID",
]


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def rankedniche_ready() -> bool:
    """RankedNiche is optional as a whole -- skip that stage until every
    RANKEDNICHE_* secret is filled in, so Auto still runs standalone."""
    missing = [name for name in RANKEDNICHE_VARS if not os.environ.get(name)]
    if missing:
        print(f"RankedNiche not fully configured (missing {missing}) -- skipping that stage.")
        return False
    return True


def main():
    if rankedniche_ready():
        print("Checking RankedNiche intake folder...")
        try:
            result = RankedNiche(
                gemini_api_key=require("GEMINI_API_KEY"),
                hf_token=os.environ.get("HF_TOKEN") or None,
                discord_webhook_url=require("RANKEDNICHE_DISCORD_WEBHOOK_URL"),
                google_client_id=require("RANKEDNICHE_GOOGLE_CLIENT_ID"),
                google_client_secret=require("RANKEDNICHE_GOOGLE_CLIENT_SECRET"),
                google_refresh_token=require("RANKEDNICHE_GOOGLE_REFRESH_TOKEN"),
                drive_intake_folder_id=require("RANKEDNICHE_DRIVE_INTAKE_FOLDER_ID"),
                drive_output_folder_id=require("RANKEDNICHE_DRIVE_OUTPUT_FOLDER_ID"),
            ).run()
            if result is None:
                print("No RankedNiche folder ready this run.")
        except Exception as e:
            # Channel.run already Discord-notified; keep going so Auto still ships.
            print(f"RankedNiche stage failed ({e}) -- continuing to Auto regardless.")
    else:
        print("RankedNiche not configured -- skipping straight to Auto.")

    print("Running Auto...")
    Auto(
        gemini_api_key=require("GEMINI_API_KEY"),
        hf_token=os.environ.get("HF_TOKEN") or None,
        pexels_api_key=require("PEXELS_API_KEY"),
        discord_webhook_url=require("AUTO_DISCORD_WEBHOOK_URL"),
        google_client_id=require("AUTO_GOOGLE_CLIENT_ID"),
        google_client_secret=require("AUTO_GOOGLE_CLIENT_SECRET"),
        google_refresh_token=require("AUTO_GOOGLE_REFRESH_TOKEN"),
        drive_folder_id=require("AUTO_DRIVE_FOLDER_ID"),
        video_topic=os.environ.get("VIDEO_TOPIC") or None,
        youtube_api_key=os.environ.get("YOUTUBE_API_KEY") or None,
        reddit_client_id=os.environ.get("REDDIT_CLIENT_ID") or None,
        reddit_client_secret=os.environ.get("REDDIT_CLIENT_SECRET") or None,
    ).run()


if __name__ == "__main__":
    main()
