"""YouTube + RSS + Wikipedia + Hacker News — Trending subclass."""

from __future__ import annotations

import re
from datetime import date
from xml.etree import ElementTree as ET

import requests

from engine.DiscordNotify import DiscordNotifier
from engine.Trending import HTTP_TIMEOUT, SourceAttempt, Trending, headers

RSS_FEEDS = [
    ("Google News", "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"),
    ("BBC", "https://feeds.bbci.co.uk/news/rss.xml"),
    ("NPR", "https://feeds.npr.org/1001/rss.xml"),
]


class News(Trending):
    def __init__(
        self,
        gemini_api_key: str,
        *,
        discord: DiscordNotifier,
        hf_token: str | None = None,
        youtube_api_key: str | None = None,
    ) -> None:
        super().__init__(gemini_api_key, discord=discord, hf_token=hf_token)
        self.youtube_api_key = youtube_api_key

    def gather(self) -> list[SourceAttempt]:
        return [
            self.fetch_youtube_popular(),
            self.fetch_rss_feeds(),
            self.fetch_wikipedia_current(),
            self.fetch_hacker_news(),
        ]

    def topic_pick_prompt(self, research_context: str) -> str:
        return (
            f"{research_context}\n\n"
            "Pick the SINGLE most compelling, story-worthy topic from the above "
            "for a short narrative YouTube video. Priority order: YouTube first, "
            "then news (RSS / Wikipedia / Hacker News). "
            "Describe it in one sentence, specific enough to write a script from -- "
            "not a vague category. Reply with ONLY that one sentence."
        )

    def parse_rss_titles(self, xml_bytes: bytes, limit: int = 8) -> list[str]:
        root = ET.fromstring(xml_bytes)
        titles: list[str] = []
        for item in root.findall("./channel/item"):
            title = (item.findtext("title") or "").strip()
            if title:
                titles.append(title)
            if len(titles) >= limit:
                return titles
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
            if title:
                titles.append(title)
            if len(titles) >= limit:
                break
        return titles

    def fetch_hacker_news(self) -> SourceAttempt:
        name = "Hacker News"
        try:
            ids = requests.get(
                "https://hacker-news.firebaseio.com/v0/topstories.json",
                headers=headers(),
                timeout=HTTP_TIMEOUT,
            )
            ids.raise_for_status()
            story_ids = ids.json()[:12]
            titles: list[str] = []
            for sid in story_ids:
                item = requests.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                    headers=headers(),
                    timeout=HTTP_TIMEOUT,
                )
                item.raise_for_status()
                data = item.json() or {}
                title = (data.get("title") or "").strip()
                if title:
                    titles.append(title)
                if len(titles) >= 10:
                    break
            if not titles:
                raise RuntimeError("no HN titles returned")
            return SourceAttempt(name=name, lines=titles)
        except Exception as e:
            print(f"{name} failed ({e}) -- skipping.")
            return SourceAttempt(name=name, skipped=True, reason=str(e))

    def fetch_wikipedia_current(self) -> SourceAttempt:
        name = "Wikipedia Current Events"
        try:
            today = date.today()
            page = (
                f"Portal:Current events/{today.year} "
                f"{today.strftime('%B')} {today.day}"
            )
            r = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "parse",
                    "page": page,
                    "prop": "wikitext",
                    "format": "json",
                    "formatversion": 2,
                },
                headers=headers(),
                timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
            payload = r.json()
            if payload.get("error"):
                raise RuntimeError(payload["error"].get("info") or str(payload["error"]))
            wikitext = (payload.get("parse") or {}).get("wikitext") or ""
            if not wikitext:
                raise RuntimeError(f"empty wikitext for {page}")

            lines: list[str] = []
            for raw_line in wikitext.splitlines():
                stripped = raw_line.lstrip()
                if not stripped.startswith("*"):
                    continue
                text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", stripped)
                text = re.sub(r"\[https?://[^\]\s]+\s+([^\]]+)\]", r"\1", text)
                text = re.sub(r"\[https?://[^\]\s]+\]", "", text)
                text = re.sub(r"\{\{[^}]+\}\}", "", text)
                text = re.sub(r"<[^>]+>", "", text)
                text = re.sub(r"'{2,3}", "", text)
                text = text.lstrip("* ").strip()
                if len(text) >= 60:
                    lines.append(text)
                if len(lines) >= 12:
                    break

            if not lines:
                raise RuntimeError(f"no event bullets parsed from {page}")
            return SourceAttempt(name=name, lines=lines)
        except Exception as e:
            print(f"{name} failed ({e}) -- skipping.")
            return SourceAttempt(name=name, skipped=True, reason=str(e))

    def fetch_rss_feeds(self) -> SourceAttempt:
        name = "RSS (Google News / BBC / NPR)"
        try:
            lines: list[str] = []
            errors: list[str] = []
            for feed_name, url in RSS_FEEDS:
                try:
                    r = requests.get(url, headers=headers(), timeout=HTTP_TIMEOUT)
                    r.raise_for_status()
                    for t in self.parse_rss_titles(r.content):
                        lines.append(f"[{feed_name}] {t}")
                except Exception as e:
                    errors.append(f"{feed_name}: {e}")
            if not lines:
                raise RuntimeError("; ".join(errors) or "all RSS feeds empty")
            if errors:
                print(f"{name}: partial failures: {errors}")
            return SourceAttempt(name=name, lines=lines)
        except Exception as e:
            print(f"{name} failed ({e}) -- skipping.")
            return SourceAttempt(name=name, skipped=True, reason=str(e))

    def fetch_youtube_popular(self) -> SourceAttempt:
        name = "YouTube Most Popular"
        if not self.youtube_api_key:
            reason = "YOUTUBE_API_KEY not set"
            print(f"{name} skipped ({reason}).")
            return SourceAttempt(name=name, skipped=True, reason=reason)
        try:
            r = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={
                    "part": "snippet",
                    "chart": "mostPopular",
                    "regionCode": "US",
                    "maxResults": 10,
                    "key": self.youtube_api_key,
                },
                headers=headers(),
                timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
            items = r.json().get("items") or []
            titles = []
            for item in items:
                title = ((item.get("snippet") or {}).get("title") or "").strip()
                channel = ((item.get("snippet") or {}).get("channelTitle") or "").strip()
                if title:
                    titles.append(f"{title} ({channel})" if channel else title)
            if not titles:
                raise RuntimeError("no YouTube popular titles")
            return SourceAttempt(name=name, lines=titles)
        except Exception as e:
            print(f"{name} failed ({e}) -- skipping.")
            return SourceAttempt(name=name, skipped=True, reason=str(e))
