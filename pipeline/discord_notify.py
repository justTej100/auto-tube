import requests


def send_review_notification(
    webhook_url: str,
    title: str,
    drive_link: str,
    *,
    topic: str | None = None,
    topic_picker: str | None = None,
    script_provider: str | None = None,
    research_sources: list[str] | None = None,
):
    """Posts a review link plus which models/sources powered this run."""
    lines = [f"🎬 New video ready for review: **{title}**", drive_link]

    if topic:
        lines.append(f"Topic: {topic}")

    details: list[str] = []
    if topic_picker:
        details.append(f"Topic pick: **{topic_picker}**")
    if script_provider:
        details.append(f"Script gen: **{script_provider}**")
    if research_sources:
        details.append(f"Research: {', '.join(research_sources)}")
    elif topic_picker is None and topic:
        details.append("Research: skipped (manual `VIDEO_TOPIC`)")

    if details:
        lines.append("")
        lines.extend(details)

    content = "\n".join(lines)
    if len(content) > 1900:
        content = content[:1890] + "\n…(truncated)"

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
