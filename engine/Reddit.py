"""Reddit OAuth top posts — Trending subclass used by Auto."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from engine.DiscordNotify import DiscordNotifier
from engine.Trending import HTTP_TIMEOUT, SourceAttempt, Trending, headers

DENVER = ZoneInfo("America/Denver")
# Publish hours (Denver local): 5am, 1pm, 8pm → slots 1, 2, 3.
SLOT_BY_HOUR = {5: 1, 13: 2, 20: 3}


class Reddit(Trending):
    def __init__(
        self,
        gemini_api_key: str,
        *,
        discord: DiscordNotifier,
        reddit_client_id: str,
        reddit_client_secret: str,
        hf_token: str | None = None,
        subreddit: str = "AmItheAsshole",
    ) -> None:
        super().__init__(gemini_api_key, discord=discord, hf_token=hf_token)
        self.reddit_client_id = reddit_client_id
        self.reddit_client_secret = reddit_client_secret
        self.subreddit = subreddit
        self._titles: list[str] = []

    def gather(self) -> list[SourceAttempt]:
        attempt = self.fetch_reddit_oauth()
        self._titles = list(attempt.lines) if not attempt.skipped else []
        return [attempt]

    def topic_pick_prompt(self, research_context: str) -> str:
        # Unused: Reddit overrides pick_topic with schedule-based selection.
        return research_context

    def scheduled_rank(self, now: datetime | None = None) -> int:
        """Odd Denver day → ranks 1–3; even day → 4–6. Slot from local hour."""
        local = now.astimezone(DENVER) if now is not None else datetime.now(DENVER)
        slot = SLOT_BY_HOUR.get(local.hour, 1)
        base = 0 if local.day % 2 == 1 else 3
        return base + slot

    def pick_topic(self, research_context: str) -> tuple[str, str]:
        titles = self._titles
        if not titles:
            raise RuntimeError("No Reddit titles available for schedule pick")
        rank = self.scheduled_rank()
        idx = min(rank, len(titles)) - 1
        topic = titles[idx]
        print(f"Schedule pick: Denver rank {rank} → #{idx + 1}: {topic}")
        return topic, f"schedule rank {rank}"

    def fetch_reddit_oauth(self) -> SourceAttempt:
        name = "Reddit OAuth"
        if not self.reddit_client_id or not self.reddit_client_secret:
            reason = "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set"
            print(f"{name} skipped ({reason}).")
            return SourceAttempt(name=name, skipped=True, reason=reason)
        try:
            token_resp = requests.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(self.reddit_client_id, self.reddit_client_secret),
                data={"grant_type": "client_credentials"},
                headers=headers(),
                timeout=HTTP_TIMEOUT,
            )
            token_resp.raise_for_status()
            access_token = token_resp.json().get("access_token")
            if not access_token:
                raise RuntimeError(
                    f"no access_token in response: {token_resp.text[:200]}"
                )

            r = requests.get(
                f"https://oauth.reddit.com/r/{self.subreddit}/top",
                params={"t": "day", "limit": 10},
                headers=headers(Authorization=f"bearer {access_token}"),
                timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
            posts = r.json()["data"]["children"]
            titles = [
                p["data"]["title"]
                for p in posts
                if not p["data"].get("over_18") and p["data"].get("title")
            ]
            if not titles:
                raise RuntimeError("no Reddit OAuth titles")
            return SourceAttempt(name=name, lines=titles)
        except Exception as e:
            print(f"{name} failed ({e}) -- skipping.")
            return SourceAttempt(name=name, skipped=True, reason=str(e))
