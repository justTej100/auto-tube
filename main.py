"""Top-level entry point: checks RankedbyHetti's intake folder first (if
that channel is configured), then always runs NewNova. RankedbyHetti
failures are logged and reported to Discord but never block NewNova --
NewNova is the pipeline that has to keep working every single run without
anyone touching it, per its design goal."""

from engine.auto import Auto
from engine.config import load_hetti_config, load_nova_config, load_shared_config
from engine.RankedNiche import RankedNiche


def main():
    shared = load_shared_config()
    hetti_cfg = load_hetti_config()

    if hetti_cfg:
        print("Checking RankedbyHetti intake folder...")
        try:
            result = RankedNiche(
                gemini_api_key=shared.gemini_api_key,
                hf_token=shared.hf_token,
                discord_webhook_url=hetti_cfg.discord_webhook_url,
                google_client_id=hetti_cfg.google_client_id,
                google_client_secret=hetti_cfg.google_client_secret,
                google_refresh_token=hetti_cfg.google_refresh_token,
                drive_intake_folder_id=hetti_cfg.drive_intake_folder_id,
                drive_output_folder_id=hetti_cfg.drive_output_folder_id,
            ).run()
            if result is None:
                print("No RankedbyHetti folder ready this run.")
        except Exception as e:
            # Channel.run already Discord-notified; keep going so Auto still ships.
            print(f"RankedbyHetti stage failed ({e}) -- continuing to NewNova regardless.")
    else:
        print("RankedbyHetti not configured -- skipping straight to NewNova.")

    nova_cfg = load_nova_config()
    print("Running NewNova...")
    Auto(
        gemini_api_key=shared.gemini_api_key,
        hf_token=shared.hf_token,
        pexels_api_key=shared.pexels_api_key,
        discord_webhook_url=nova_cfg.discord_webhook_url,
        google_client_id=nova_cfg.google_client_id,
        google_client_secret=nova_cfg.google_client_secret,
        google_refresh_token=nova_cfg.google_refresh_token,
        drive_folder_id=nova_cfg.drive_folder_id,
        video_topic=shared.video_topic,
        youtube_api_key=shared.youtube_api_key,
        reddit_client_id=shared.reddit_client_id,
        reddit_client_secret=shared.reddit_client_secret,
    ).run()


if __name__ == "__main__":
    main()
