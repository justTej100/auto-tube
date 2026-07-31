"""Trending-topic research from multiple live sources, encapsulated in a
`Trending` object.

Order (Gemini grounded search always first):
  1. Gemini Google Search grounding
  2. Hacker News
  3. Wikipedia featured/current news
  4. RSS (Google News, BBC, NPR)
  5. YouTube most popular
  6. Reddit anonymous .json
  7. Reddit OAuth (registered app)

Each source fails soft -- skipped sources are Discord-notified.
If every source fails, research() raises. Topic picking is a separate
plain Gemini call (no grounding), with optional HF fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from xml.etree import ElementTree as ET

import requests
from google import genai
from google.genai import types
from huggingface_hub import InferenceClient

from engine.DiscordNotify import DiscordNotifier

USER_AGENT = "dailydose-research-bot/1.0 (github actions; contact: local)"
HTTP_TIMEOUT = 20

RSS_FEEDS = [
    ("Google News", "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"),
    ("BBC", "https://feeds.bbci.co.uk/news/rss.xml"),
    ("NPR", "https://feeds.npr.org/1001/rss.xml"),
]


@dataclass(frozen=True)
class TopicResearch:
    """Picked topic plus the raw research brief fed into script writing."""
    topic: str
    research_context: str
    skipped_sources: tuple[str, ...] = ()
    used_sources: tuple[str, ...] = ()
    # "Gemini" or "Hugging Face" — which model picked the topic.
    topic_picker: str = "Gemini"


@dataclass
class SourceAttempt:
    name: str
    lines: list[str] = field(default_factory=list)
    skipped: bool = False
    reason: str = ""


# ---------------------------------------------------------------------------
# Small stateless helpers (no need for `self`)
# ---------------------------------------------------------------------------

def headers(**extra: str) -> dict[str, str]:
    h = {"User-Agent": USER_AGENT}
    h.update(extra)
    return h


def as_bullets(lines: list[str], limit: int = 10) -> str:
    return "\n".join(f"- {line}" for line in lines[:limit])


def parse_rss_titles(xml_bytes: bytes, limit: int = 8) -> list[str]:
    root = ET.fromstring(xml_bytes)
    titles: list[str] = []
    # RSS 2.0
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        if title:
            titles.append(title)
        if len(titles) >= limit:
            return titles
    # Atom
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        if title:
            titles.append(title)
        if len(titles) >= limit:
            break
    return titles


# ---------------------------------------------------------------------------
# Trending: orchestrates every source + topic picking
# ---------------------------------------------------------------------------

class Trending:
    """Gathers live trending research and picks today's topic.

    Usage:
        trending = Trending(
            gemini_api_key=...,
            discord=channel.discord,
            hf_token=...,
            youtube_api_key=...,
            reddit_client_id=...,
            reddit_client_secret=...,
        )
        result = trending.research()  # -> TopicResearch
    """

    def __init__(
        self,
        gemini_api_key: str,
        *,
        discord: DiscordNotifier,
        hf_token: str | None = None,
        youtube_api_key: str | None = None,
        reddit_client_id: str | None = None,
        reddit_client_secret: str | None = None,
    ) -> None:
        self.gemini_api_key = gemini_api_key
        self.discord = discord
        self.hf_token = hf_token
        self.youtube_api_key = youtube_api_key
        self.reddit_client_id = reddit_client_id
        self.reddit_client_secret = reddit_client_secret

    # -- Individual sources --------------------------------------------

    def fetch_gemini_grounded(self) -> SourceAttempt:
        name = "Gemini Google Search"
        try:
            client = genai.Client(api_key=self.gemini_api_key)
            response = client.models.generate_content(
                model="gemini-3-flash-preview",
                contents=(
                    "Search for what's trending in news, culture, and social media "
                    "right now. List 5 specific current stories or topics (not vague "
                    "categories) that would make a compelling short story-driven video "
                    "for a general 'daily dose of life' YouTube audience. One line each."
                ),
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                ),
            )
            text = (response.text or "").strip()
            if not text:
                raise RuntimeError("empty grounded response")
            lines = [ln.lstrip("-•* ").strip() for ln in text.splitlines() if ln.strip()]
            return SourceAttempt(name=name, lines=lines)
        except Exception as e:
            print(f"{name} failed ({e}) -- skipping.")
            return SourceAttempt(name=name, skipped=True, reason=str(e))

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
                # Flatten wiki links [[Target|Label]] -> Label, drop templates/refs.
                text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", stripped)
                text = re.sub(r"\[https?://[^\]\s]+\s+([^\]]+)\]", r"\1", text)
                text = re.sub(r"\[https?://[^\]\s]+\]", "", text)
                text = re.sub(r"\{\{[^}]+\}\}", "", text)
                text = re.sub(r"<[^>]+>", "", text)
                text = re.sub(r"'{2,3}", "", text)
                text = text.lstrip("* ").strip()
                # Prefer concrete event sentences over short section headers.
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
                    titles = parse_rss_titles(r.content)
                    for t in titles:
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
                raise RuntimeError(f"no access_token in response: {token_resp.text[:200]}")

            r = requests.get(
                "https://oauth.reddit.com/r/all/top",
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

    # -- Topic picking ----------------------------------------------------

    def pick_topic_with_gemini(self, research_context: str) -> str:
        client = genai.Client(api_key=self.gemini_api_key)
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=(
                f"{research_context}\n\n"
                "Pick the SINGLE most compelling, story-worthy topic from the above "
                "for a short narrative YouTube video. Describe it in one sentence, "
                "specific enough to write a script from -- not a vague category. "
                "Reply with ONLY that one sentence."
            ),
        )
        topic = (response.text or "").strip()
        if not topic:
            raise RuntimeError("Gemini topic picker returned empty text")
        return topic

    def pick_topic_with_huggingface(self, research_context: str) -> str:
        client = InferenceClient(api_key=self.hf_token)
        completion = client.chat.completions.create(
            model="meta-llama/Llama-3.1-8B-Instruct",
            messages=[
                {
                    "role": "system",
                    "content": "Reply with ONLY one sentence naming the chosen topic.",
                },
                {
                    "role": "user",
                    "content": (
                        f"{research_context}\n\n"
                        "Pick the SINGLE most compelling, story-worthy topic from the above "
                        "for a short narrative YouTube video. One sentence only."
                    ),
                },
            ],
            max_tokens=120,
            temperature=0.4,
        )
        topic = (completion.choices[0].message.content or "").strip()
        if not topic:
            raise RuntimeError("HF topic picker returned empty text")
        return topic

    # -- Orchestration ------------------------------------------------------

    def research(self) -> TopicResearch:
        """Runs every research source (Gemini grounded search first). Skipped
        sources are reported to Discord. Raises only if nothing usable remains."""

        # Gemini first, then the rest in a stable order.
        attempts: list[SourceAttempt] = [
            self.fetch_reddit_oauth(),
            self.fetch_youtube_popular(),
            self.fetch_gemini_grounded(),
            self.fetch_hacker_news(),
            self.fetch_wikipedia_current(),
            self.fetch_rss_feeds(),
        ]

        successful = [a for a in attempts if not a.skipped and a.lines]
        skipped = [a for a in attempts if a.skipped]

        if skipped:
            skip_lines = [f"**{a.name}**: {a.reason}" for a in skipped]
            try:
                self.discord.send_research_skip(skip_lines)
                print(f"Discord notified about {len(skipped)} skipped research source(s).")
            except Exception as e:
                # Don't fail the run just because Discord skip notify failed.
                print(f"Discord skip notification failed ({e})")

        if not successful:
            names = ", ".join(a.name for a in attempts)
            raise RuntimeError(
                f"Trending research failed: every source was unavailable ({names}). "
                "Set VIDEO_TOPIC to override, or retry later."
            )

        sections = [f"### {a.name}\n{as_bullets(a.lines)}" for a in successful]
        research_context = (
            f"Live research gathered {date.today().isoformat()} UTC:\n\n"
            + "\n\n".join(sections)
        )

        try:
            topic = self.pick_topic_with_gemini(research_context)
            topic_picker = "Gemini"
        except Exception as e:
            if not self.hf_token:
                raise RuntimeError(f"Topic picking failed and no HF_TOKEN set: {e}") from e
            print(f"Gemini topic pick failed ({e}). Falling back to Hugging Face.")
            topic = self.pick_topic_with_huggingface(research_context)
            topic_picker = "Hugging Face"

        return TopicResearch(
            topic=topic,
            research_context=research_context,
            skipped_sources=tuple(a.name for a in skipped),
            used_sources=tuple(a.name for a in successful),
            topic_picker=topic_picker,
        )