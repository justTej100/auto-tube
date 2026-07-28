import requests


def send_review_notification(webhook_url: str, title: str, drive_link: str):
    """Posts a message into the configured Discord channel via webhook —
    no bot, no login, just a POST to the webhook URL."""
    content = f'🎬 New video ready for review: **{title}**\n{drive_link}'
    resp = requests.post(webhook_url, json={"content": content}, timeout=30)
    resp.raise_for_status()


def send_research_skip_notification(webhook_url: str, skipped: list[str]):
    """Notifies Discord that one or more trending-research sources were
    skipped (failed or missing credentials) during this run."""
    if not skipped:
        return

    body = "\n".join(f"• {line}" for line in skipped)
    content = (
        "⚠️ Trending research skipped one or more sources this run:\n"
        f"{body}"
    )
    # Discord content limit is 2000 chars; truncate reasons if needed.
    if len(content) > 1900:
        content = content[:1890] + "\n…(truncated)"

    resp = requests.post(
        webhook_url,
        json={"content": content, "username": "auto-tube research"},
        timeout=30,
    )
    resp.raise_for_status()
