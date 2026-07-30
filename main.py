"""Top-level entry point: checks RankedbyHetti's intake folder first (if
that channel is configured), then always runs NewNova. RankedbyHetti
failures are logged and reported to Discord but never block NewNova --
NewNova is the pipeline that has to keep working every single run without
anyone touching it, per its design goal."""

from core.config import load_hetti_config, load_nova_config, load_shared_config
from core.discord_notify import send_message

from pipelines.newnova import main as newnova
from pipelines.rankedbyhetti import main as rankedbyhetti


def main():
    shared = load_shared_config()
    hetti_cfg = load_hetti_config()

    if hetti_cfg:
        print("Checking RankedbyHetti intake folder...")
        try:
            result = rankedbyhetti.run(shared, hetti_cfg)
            if result is None:
                print("No RankedbyHetti folder ready this run.")
        except Exception as e:
            print(f"RankedbyHetti stage failed ({e}) -- continuing to NewNova regardless.")
            try:
                send_message(
                    hetti_cfg.discord_webhook_url,
                    f"⚠️ RankedbyHetti run failed: {e}",
                    username="rankedbyhetti",
                )
            except Exception as notify_err:
                print(f"Also failed to notify Discord about the RankedbyHetti failure: {notify_err}")
    else:
        print("RankedbyHetti not configured -- skipping straight to NewNova.")

    nova_cfg = load_nova_config()
    print("Running NewNova...")
    newnova.run(shared, nova_cfg)


if __name__ == "__main__":
    main()