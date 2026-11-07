"""Top-level entry point: checks RankedNiche's intake folder first (if
that channel is configured), then always runs Auto. RankedNiche failures
are logged and reported to Discord but never block Auto -- Auto is the
channel that has to keep working every single run without anyone touching
it, per its design goal."""

import os
from pathlib import Path

from engine.Auto import Auto
from engine.RankedNiche import RankedNiche




def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value



def main():

    drive = Drive(        google_client_id=require("AUTO_GOOGLE_CLIENT_ID"),
        google_client_secret=require("AUTO_GOOGLE_CLIENT_SECRET"),
        google_refresh_token=require("AUTO_GOOGLE_REFRESH_TOKEN"),
        drive_intake_folder_id=require("RANKEDNICHE_DRIVE_INTAKE_FOLDER_ID"),
                drive_output_folder_id=require("RANKEDNICHE_DRIVE_OUTPUT_FOLDER_ID")
    )


    persona = Persona(
        name="John Doe",
        description="A friendly and helpful person",
        voice=Voice(voice_wav=Path("assets/auto/voice_reference.wav"), voice_mp3=Path("assets/auto/voice_reference.mp3"), voice_converted=Path("build/auto/voice_reference_converted.wav"))
    gemini_api_key=require("GEMINI_API_KEY"),
                hf_token=os.environ.get("HF_TOKEN") or None,
    )
            
   
    voice = (os.environ.get("AUTO_VOICE") or "voice_reference").strip()
    voice_mp3 = Path("assets/auto") / f"{voice}.mp3"
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
        reddit_client_id=os.environ.get("REDDIT_CLIENT_ID") or None,
        reddit_client_secret=os.environ.get("REDDIT_CLIENT_SECRET") or None,
        voice_wav=voice_mp3.with_suffix(".wav"),
        voice_mp3=voice_mp3,
        voice_converted=Path("build/auto") / f"{voice_mp3.stem}_converted.wav",
    ).run()


if __name__ == "__main__":
    main()
