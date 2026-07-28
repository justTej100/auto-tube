from datetime import date
from pathlib import Path

from pipeline.assemble import build_segment_clip, concat_clips, mix_background_music
from pipeline.config import Config
from pipeline.drive_delivery import upload_to_drive
from pipeline.discord_notify import send_review_notification
from pipeline.script_gen import generate_script
from pipeline.trending import pick_todays_topic
from pipeline.visuals import fetch_video
from pipeline.voice import synthesize_speech

WORKDIR = Path("build")


def main():
    cfg = Config()
    WORKDIR.mkdir(exist_ok=True)

    research_context = None
    if cfg.video_topic:
        topic = cfg.video_topic
        print(f"Using provided topic: {topic}")
    else:
        print(
            "Researching today's trending topic "
            "(Gemini grounded search first, then HN / Wikipedia / RSS / "
            "YouTube / Reddit)..."
        )
        research = pick_todays_topic(
            cfg.gemini_api_key,
            discord_webhook_url=cfg.discord_webhook_url,
            hf_token=cfg.hf_token,
            youtube_api_key=cfg.youtube_api_key,
            reddit_client_id=cfg.reddit_client_id,
            reddit_client_secret=cfg.reddit_client_secret,
        )
        topic = research.topic
        research_context = research.research_context
        if research.skipped_sources:
            print(f"Skipped sources: {', '.join(research.skipped_sources)}")
        print(f"Topic selected: {topic}")

    script = generate_script(
        cfg.gemini_api_key,
        topic,
        cfg.hf_token,
        research_context=research_context,
    )

    clip_paths = []
    for i, seg in enumerate(script["segments"]):
        print(f"Segment {i}: {seg['narration'][:60]}...")
        audio_path = WORKDIR / f"seg_{i}.wav"
        video_path = WORKDIR / f"seg_{i}_source.mp4"
        clip_path = WORKDIR / f"seg_{i}.mp4"

        synthesize_speech(seg["narration"], audio_path)
        fetch_video(cfg.pexels_api_key, seg["image_query"], video_path)
        build_segment_clip(video_path, audio_path, clip_path, seg["narration"], WORKDIR)
        clip_paths.append(clip_path)

    final_path = WORKDIR / "final.mp4"
    concat_clips(clip_paths, WORKDIR, final_path)
    print(f"Video assembled: {final_path}")

    music_path = Path("assets/background_music.mp3")
    if music_path.exists():
        mixed_path = WORKDIR / "final_with_music.mp4"
        mix_background_music(final_path, music_path, mixed_path)
        final_path = mixed_path
        print("Background music mixed in.")
    else:
        print("No assets/background_music.mp3 found -- skipping music (optional).")

    filename = f"{date.today().isoformat()} - {script['title']}.mp4"
    drive_link = upload_to_drive(
        cfg.google_client_id, cfg.google_client_secret, cfg.google_refresh_token,
        cfg.drive_folder_id, final_path, filename,
    )
    print(f"Uploaded to Drive: {drive_link}")

    send_review_notification(cfg.discord_webhook_url, script["title"], drive_link)
    print("Discord notification sent.")


if __name__ == "__main__":
    main()
