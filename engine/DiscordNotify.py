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
    """Builds and sends Discord webhook messages. Channel-agnostic --
    each channel passes its own webhook_url, since Auto and RankedNiche
    post to different webhooks."""

    def __init__(
        self,
        webhook_url: str,
        *,
        timeout: int = 30,
        session: Any = requests,
        max_content_length: int = SAFE_CONTENT_LIMIT,
        default_username: str | None = None,
    ):
        if max_content_length > DISCORD_CONTENT_LIMIT:
            raise ValueError("max_content_length cannot exceed Discord's 2000 character limit")

        self.webhook_url = webhook_url
        self.timeout = timeout
        self.session = session
        self.max_content_length = max_content_length
        self.default_username = default_username

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
        research_findings: Sequence[tuple[str, Sequence[str]]] | None = None,
        username: str | None = None,
    ) -> None:
        self.send(
            self.build_review_message(
                title,
                drive_link,
                topic=topic,
                topic_picker=topic_picker,
                script_provider=script_provider,
                research_findings=research_findings,
                username=username,
            )
        )

    def send_research_skip(self, skipped: Sequence[str]) -> None:
        message = self.build_research_skip_message(skipped)
        if message:
            self.send(message)

    def send_error(self, error: BaseException, *, username: str | None = None) -> None:
        self.send(
            DiscordMessage(
                content=self.truncate(f"⚠️ Run failed: {error}"),
                username=username or self.default_username,
            )
        )

    def build_review_message(
        self,
        title: str,
        drive_link: str,
        *,
        topic: str | None = None,
        topic_picker: str | None = None,
        script_provider: str | None = None,
        research_findings: Sequence[tuple[str, Sequence[str]]] | None = None,
        username: str | None = None,
    ) -> DiscordMessage:
        """Posts a review link plus which models/sources powered this run."""
        lines = [f"🎬 New video ready for review: **{title}**", drive_link]

        if topic:
            lines.append(f"Topic: {topic}")

        details = self.review_details(
            topic=topic,
            topic_picker=topic_picker,
            script_provider=script_provider,
            research_findings=research_findings,
        )
        if details:
            lines.append("")
            lines.extend(details)

        return DiscordMessage(
            content=self.truncate("\n".join(lines)),
            username=username or self.default_username,
        )

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
            content=self.truncate(content),
            username="auto-tube research",
        )

    def review_details(
        self,
        *,
        topic: str | None,
        topic_picker: str | None,
        script_provider: str | None,
        research_findings: Sequence[tuple[str, Sequence[str]]] | None,
    ) -> list[str]:
        details: list[str] = []
        if topic_picker:
            details.append(f"Topic pick: **{topic_picker}**")
        if script_provider:
            details.append(f"Script gen: **{script_provider}**")
        if research_findings:
            details.append("Research choices:")
            for source, findings in research_findings:
                details.append(f"**{source}**")
                for finding in findings:
                    clipped = finding if len(finding) <= 160 else finding[:157] + "…"
                    details.append(f"• {clipped}")
        elif topic_picker is None and topic:
            details.append("Research: skipped (manual `VIDEO_TOPIC`)")
        return details

    def truncate(self, content: str) -> str:
        if len(content) <= self.max_content_length:
            return content

        keep_chars = self.max_content_length - len(TRUNCATION_SUFFIX)
        return content[:keep_chars] + TRUNCATION_SUFFIX


def send_message(webhook_url: str, content: str, username: str | None = None) -> None:
    """Plain free-text notification for callers that don't own a Channel
    (e.g. main.py reporting a RankedNiche-stage failure)."""
    DiscordNotifier(webhook_url).send(DiscordMessage(content=content, username=username))
