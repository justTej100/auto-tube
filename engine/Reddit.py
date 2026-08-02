"""Reddit OAuth top posts — Trending subclass used by Auto."""

from __future__ import annotations

import requests

from engine.DiscordNotify import DiscordNotifier
from engine.Trending import HTTP_TIMEOUT, SourceAttempt, Trending, headers


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

    def gather(self) -> list[SourceAttempt]:
        return [self.fetch_reddit_oauth()]

    def topic_pick_prompt(self, research_context: str) -> str:
        return (
            f"{research_context}\n\n"
            "Pick ONE specific Reddit post from the section above and turn it into "
            "the topic for a short narrative YouTube video ABOUT THAT REDDIT STORY -- "
            "what the post is about, what happened, why people cared. Describe the "
            "Reddit story in one sentence, specific enough to write a script from. "
            "Reply with ONLY that one sentence."
        )

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
