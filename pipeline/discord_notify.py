from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import requests

DISCORD_CONTENT_LIMIT = 2000
SAFE_CONTENT_LIMIT = 1900
TRUNCATION_SUFFIX = "\n…(truncated)"


@dataclass(frozen=True)
class DiscordMessage:
    content: str
    username: str | None = None

    def payload(self) -> dict[str, str]:
        data = {"content": self.content}
        if self.username:
            data["username"] = self.username
        return data


class DiscordNotifier:
    """Builds and sends Discord webhook messages for pipeline events."""

    def __init__(
        self,
        webhook_url: str,
        *,
        timeout: int = 30,
        session: Any = requests,
        max_content_length: int = SAFE_CONTENT_LIMIT,
    ):
        if max_content_length > DISCORD_CONTENT_LIMIT:
            raise ValueError("max_content_length cannot exceed Discord's 2000 character limit")

        self.webhook_url = webhook_url
        self.timeout = timeout
        self.session = session
        self.max_content_length = max_content_length

    def send(self, message: DiscordMessage) -> None:
        resp = self.session.post(
            self.webhook_url,
            json=message.payload(),
            timeout=self.timeout,
        )
        resp.raise_for_status()

    def send_review(
        self,
        title: str,
        drive_link: str,
        *,
        topic: str | None = None,
        topic_picker: str | None = None,
        script_provider: str | None = None,
        research_sources: Sequence[str] | None = None,
    ) -> None:
        self.send(
            self.build_review_message(
                title,
                drive_link,
                topic=topic,
                topic_picker=topic_picker,
                script_provider=script_provider,
                research_sources=research_sources,
            )
        )

    def send_research_skip(self, skipped: Sequence[str]) -> None:
        message = self.build_research_skip_message(skipped)
        if message:
            self.send(message)

    def build_review_message(
        self,
        title: str,
        drive_link: str,
        *,
        topic: str | None = None,
        topic_picker: str | None = None,
        script_provider: str | None = None,
        research_sources: Sequence[str] | None = None,
    ) -> DiscordMessage:
        """Posts a review link plus which models/sources powered this run."""
        lines = [f"🎬 New video ready for review: **{title}**", drive_link]

        if topic:
            lines.append(f"Topic: {topic}")

        details = self._review_details(
            topic=topic,
            topic_picker=topic_picker,
            script_provider=script_provider,
            research_sources=research_sources,
        )
        if details:
            lines.append("")
            lines.extend(details)

        return DiscordMessage(content=self._truncate("\n".join(lines)))

    def build_research_skip_message(self, skipped: Sequence[str]) -> DiscordMessage | None:
        """Notifies Discord that one or more trending-research sources were
        skipped (failed or missing credentials) during this run."""
        if not skipped:
            return None

        body = "\n".join(f"• {line}" for line in skipped)
        content = (
            "⚠️ Trending research skipped one or more sources this run:\n"
            f"{body}"
        )
        return DiscordMessage(
            content=self._truncate(content),
            username="auto-tube research",
        )

    def _review_details(
        self,
        *,
        topic: str | None,
        topic_picker: str | None,
        script_provider: str | None,
        research_sources: Sequence[str] | None,
    ) -> list[str]:
        details: list[str] = []
        if topic_picker:
            details.append(f"Topic pick: **{topic_picker}**")
        if script_provider:
            details.append(f"Script gen: **{script_provider}**")
        if research_sources:
            details.append(f"Research: {', '.join(research_sources)}")
        elif topic_picker is None and topic:
            details.append("Research: skipped (manual `VIDEO_TOPIC`)")
        return details

    def _truncate(self, content: str) -> str:
        if len(content) <= self.max_content_length:
            return content

        keep_chars = self.max_content_length - len(TRUNCATION_SUFFIX)
        return content[:keep_chars] + TRUNCATION_SUFFIX


def send_review_notification(
    webhook_url: str,
    title: str,
    drive_link: str,
    *,
    topic: str | None = None,
    topic_picker: str | None = None,
    script_provider: str | None = None,
    research_sources: Sequence[str] | None = None,
) -> None:
    DiscordNotifier(webhook_url).send_review(
        title,
        drive_link,
        topic=topic,
        topic_picker=topic_picker,
        script_provider=script_provider,
        research_sources=research_sources,
    )


def send_research_skip_notification(webhook_url: str, skipped: Sequence[str]) -> None:
    DiscordNotifier(webhook_url).send_research_skip(skipped)
