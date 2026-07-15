import requests


def send_review_notification(webhook_url: str, title: str, drive_link: str):
    """Posts a message into the configured Discord channel via webhook —
    no bot, no login, just a POST to the webhook URL."""
    content = f'🎬 New video ready for review: **{title}**\n{drive_link}'
    resp = requests.post(webhook_url, json={"content": content}, timeout=30)
    resp.raise_for_status()
