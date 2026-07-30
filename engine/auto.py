"""Auto: fully autonomous channel production. Researches (or takes a
given) topic, writes a freeform narrative script with Gemini + Hugging
Face fallback, pulls Pexels stock footage per segment, assembles with
background music, uploads, notifies. No human input needed once
configured -- this is the pattern NewNova runs on."""

import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
from google.genai import types
from huggingface_hub import InferenceClient

from studio.channel import Channel
from studio.discord_notify import send_research_skip_notification, send_review_notification
from studio.json_utils import extract_json
from studio.quality import QUALITY_THRESHOLD, score_script

USER_AGENT = "dailydose-research-bot/1.0 (github actions; contact: local)"
HTTP_TIMEOUT = 20
MAX_QUALITY_RETRIES = 3
MAX_HF_JSON_RETRIES = 3

RSS_FEEDS = [
    ("Google News", "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"),
    ("BBC", "https://feeds.bbci.co.uk/news/rss.xml"),
    ("NPR", "https://feeds.npr.org/1001/rss.xml"),
]

SCRIPT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "narration": {"type": "string"},
                    "image_query": {"type": "string"},
                },
                "required": ["narration", "image_query"],
                "additionalProperties": False,
            },
            "minItems": 6,
            "maxItems": 10,
        },
    },
    "required": ["title", "description", "segments"],
    "additionalProperties": False,
}

SCRIPT_PROMPT_TEMPLATE = """Write a script for a short, punchy YouTube video about: {topic}
{research_block}
You're writing for a fast-scrolling audience who will swipe away in 2
seconds if they're not hooked immediately. Follow these rules:

- Ground the script in the research above when it's provided -- use real
  current details, names, numbers, and angles from that material. Do not
  invent a generic "surprising fact" that ignores what's actually trending.
- Segment 1 MUST open with a surprising claim, a question, or a
  "you'd think X, but actually Y" twist -- never a boring setup line like
  "Did you know..." or "Today we're talking about...". Earn the next
  3 seconds.
- Vary sentence rhythm. Mix short punchy lines with longer ones. Avoid
  robotic uniform pacing where every segment is the same length and shape.
- Write like a person talking to a friend, not a Wikipedia summary. Use
  natural spoken phrasing, not formal written English.
- Never use em dashes, en dashes, or hyphen-as-pause in narration
  (no "—", "–", or "word - word"). Use commas, periods, or short new
  sentences instead. Captions are burned on screen for TikTok / Shorts.
- Avoid AI-writing tells: no "leverage", "delve", "landscape", "robust",
  "testament", "pivotal", "seamless", or similar corporate/AI vocabulary.
  No "it's not just X, it's Y" constructions. No vague "experts believe"
  attributions -- be specific or don't claim it.
- Include concrete numbers, measurements, or named specifics wherever
  possible instead of vague claims.
- End on a payoff, a twist, or a thought that lingers -- not a flat
  restatement of the topic.
- For each segment's image_query, write a SPECIFIC stock-VIDEO search
  query (3-6 concrete words) that a Pexels video search can match -- not
  abstract concepts. Prefer filmable vertical/portrait scenes with clear
  subjects and action (e.g. "crowded subway platform rush", "scientist
  lab microscope closeup") over vague themes ("emotion", "future",
  "technology").

Return JSON in this exact shape:
{{
  "title": "...",
  "description": "...",
  "segments": [
    {{"narration": "one or two sentences", "image_query": "specific vivid video search, 3-6 words"}},
    ... (6 to 10 segments total)
  ]
}}

JSON rules: use double quotes only, no trailing commas, no comments,
and escape any double quotes inside string values as \\". """


@dataclass
class _SourceAttempt:
    name: str
    lines: list[str] = field(default_factory=list)
    skipped: bool = False
    reason: str = ""


@dataclass(frozen=True)
class _AutoContext:
    """What _prepare() gathers before scripting: the topic plus whatever
    research backed it (empty if VIDEO_TOPIC was set manually)."""
    topic: str
    research_context: str | None
    topic_picker: str | None
    research_sources: tuple[str, ...]


class Auto(Channel):
    def __init__(self, gemini_api_key: str, hf_token: str | None,
                 pexels_api_key: str, discord_webhook_url: str,
                 google_client_id: str, google_client_secret: str, google_refresh_token: str,
                 drive_folder_id: str,
                 video_topic: str | None = None,
                 youtube_api_key: str | None = None,
                 reddit_client_id: str | None = None, reddit_client_secret: str | None = None,
                 workdir: Path = Path("build/auto"),
                 voice_wav: Path = Path("assets/auto/voice_reference.wav"),
                 voice_mp3: Path = Path("assets/auto/voice_reference.mp3"),
                 voice_converted: Path = Path("build/auto/voice_reference_converted.wav"),
                 music_path: Path = Path("assets/auto/background_music.mp3")):
        super().__init__(
            gemini_api_key, hf_token, workdir, voice_wav, voice_mp3, voice_converted,
            google_client_id, google_client_secret, google_refresh_token,
        )
        self.pexels_api_key = pexels_api_key
        self.discord_webhook_url = discord_webhook_url
        self.drive_folder_id = drive_folder_id
        self.video_topic = video_topic
        self.youtube_api_key = youtube_api_key
        self.reddit_client_id = reddit_client_id
        self.reddit_client_secret = reddit_client_secret
        self.music_path = music_path

    # =========================================================
    # Template hooks
    # =========================================================

    def _prepare(self) -> _AutoContext:
        if self.video_topic:
            print(f"Using provided topic: {self.video_topic}")
            return _AutoContext(topic=self.video_topic, research_context=None,
                                 topic_picker=None, research_sources=())

        print("Researching today's trending topic "
              "(Gemini grounded search first, then HN / Wikipedia / RSS / "
              "YouTube / Reddit)...")
        topic, research_context, topic_picker, sources = self._research_topic()
        print(f"Topic selected via {topic_picker}: {topic}")
        return _AutoContext(topic=topic, research_context=research_context,
                             topic_picker=topic_picker, research_sources=sources)

    def _generate_script(self, context: _AutoContext) -> dict:
        script, provider = self._generate_script_with_fallback(context.topic, context.research_context)
        self._script_provider = provider   # stashed for _deliver()'s notification
        print(f"Script generated via {provider}")
        return script

    def _render_segments(self, script: dict, context: _AutoContext) -> list[Path]:
        clip_paths = []
        for i, seg in enumerate(script["segments"]):
            print(f"Segment {i}: {seg['narration'][:60]}...")
            audio_path = self.workdir / f"seg_{i}.wav"
            video_path = self.workdir / f"seg_{i}_source.mp4"
            clip_path = self.workdir / f"seg_{i}.mp4"

            self.synthesize_speech(seg["narration"], audio_path)
            self.fetch_stock_video(seg["image_query"], video_path)
            self.build_segment_clip(video_path, audio_path, clip_path, seg["narration"])
            clip_paths.append(clip_path)
        return clip_paths

    def _finalize_assembly(self, clips: list[Path], script: dict, context: _AutoContext) -> Path:
        final_path = self.workdir / "final.mp4"
        self.concat_clips(clips, final_path)
        print(f"Video assembled: {final_path}")

        if self.music_path.exists():
            mixed_path = self.workdir / "final_with_music.mp4"
            self.mix_background_music(final_path, mixed_path)
            final_path = mixed_path
            print("Background music mixed in.")
        else:
            print(f"No {self.music_path} found -- skipping music (optional).")

        return final_path

    def _deliver(self, final_path: Path, script: dict, context: _AutoContext) -> str:
        filename = f"{date.today().isoformat()} - {script['title']}.mp4"
        drive_link = self.upload_to_drive(self.drive_folder_id, final_path, filename)
        print(f"Uploaded to Drive: {drive_link}")

        send_review_notification(
            self.discord_webhook_url,
            script["title"],
            drive_link,
            topic=context.topic,
            topic_picker=context.topic_picker,
            script_provider=self._script_provider,
            research_sources=list(context.research_sources) or None,
        )
        print("Discord notification sent.")
        return drive_link

    # =========================================================
    # Auto-only: trending research
    # =========================================================

    def _headers(self, **extra: str) -> dict[str, str]:
        h = {"User-Agent": USER_AGENT}
        h.update(extra)
        return h

    def _as_bullets(self, lines: list[str], limit: int = 10) -> str:
        return "\n".join(f"- {line}" for line in lines[:limit])

    def _fetch_gemini_grounded(self) -> _SourceAttempt:
        name = "Gemini Google Search"
        try:
            response = self._client().models.generate_content(
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
            return _SourceAttempt(name=name, lines=lines)
        except Exception as e:
            print(f"{name} failed ({e}) -- skipping.")
            return _SourceAttempt(name=name, skipped=True, reason=str(e))

    def _fetch_hacker_news(self) -> _SourceAttempt:
        name = "Hacker News"
        try:
            ids = requests.get(
                "https://hacker-news.firebaseio.com/v0/topstories.json",
                headers=self._headers(), timeout=HTTP_TIMEOUT,
            )
            ids.raise_for_status()
            story_ids = ids.json()[:12]
            titles: list[str] = []
            for sid in story_ids:
                item = requests.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                    headers=self._headers(), timeout=HTTP_TIMEOUT,
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
            return _SourceAttempt(name=name, lines=titles)
        except Exception as e:
            print(f"{name} failed ({e}) -- skipping.")
            return _SourceAttempt(name=name, skipped=True, reason=str(e))

    def _fetch_wikipedia_current(self) -> _SourceAttempt:
        name = "Wikipedia Current Events"
        try:
            today = date.today()
            page = f"Portal:Current events/{today.year} {today.strftime('%B')} {today.day}"
            r = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "parse", "page": page, "prop": "wikitext",
                        "format": "json", "formatversion": 2},
                headers=self._headers(), timeout=HTTP_TIMEOUT,
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
            return _SourceAttempt(name=name, lines=lines)
        except Exception as e:
            print(f"{name} failed ({e}) -- skipping.")
            return _SourceAttempt(name=name, skipped=True, reason=str(e))

    def _parse_rss_titles(self, xml_bytes: bytes, limit: int = 8) -> list[str]:
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

    def _fetch_rss_feeds(self) -> _SourceAttempt:
        name = "RSS (Google News / BBC / NPR)"
        try:
            lines: list[str] = []
            errors: list[str] = []
            for feed_name, url in RSS_FEEDS:
                try:
                    r = requests.get(url, headers=self._headers(), timeout=HTTP_TIMEOUT)
                    r.raise_for_status()
                    for t in self._parse_rss_titles(r.content):
                        lines.append(f"[{feed_name}] {t}")
                except Exception as e:
                    errors.append(f"{feed_name}: {e}")
            if not lines:
                raise RuntimeError("; ".join(errors) or "all RSS feeds empty")
            if errors:
                print(f"{name}: partial failures: {errors}")
            return _SourceAttempt(name=name, lines=lines)
        except Exception as e:
            print(f"{name} failed ({e}) -- skipping.")
            return _SourceAttempt(name=name, skipped=True, reason=str(e))

    def _fetch_youtube_popular(self) -> _SourceAttempt:
        name = "YouTube Most Popular"
        if not self.youtube_api_key:
            reason = "YOUTUBE_API_KEY not set"
            print(f"{name} skipped ({reason}).")
            return _SourceAttempt(name=name, skipped=True, reason=reason)
        try:
            r = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "snippet", "chart": "mostPopular", "regionCode": "US",
                        "maxResults": 10, "key": self.youtube_api_key},
                headers=self._headers(), timeout=HTTP_TIMEOUT,
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
            return _SourceAttempt(name=name, lines=titles)
        except Exception as e:
            print(f"{name} failed ({e}) -- skipping.")
            return _SourceAttempt(name=name, skipped=True, reason=str(e))

    def _fetch_reddit_json(self) -> _SourceAttempt:
        name = "Reddit .json"
        try:
            r = requests.get(
                "https://www.reddit.com/r/all/top.json",
                params={"t": "day", "limit": 10}, headers=self._headers(), timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
            posts = r.json()["data"]["children"]
            titles = [p["data"]["title"] for p in posts
                      if not p["data"].get("over_18") and p["data"].get("title")]
            if not titles:
                raise RuntimeError("no Reddit .json titles")
            return _SourceAttempt(name=name, lines=titles)
        except Exception as e:
            print(f"{name} failed ({e}) -- skipping.")
            return _SourceAttempt(name=name, skipped=True, reason=str(e))

    def _fetch_reddit_oauth(self) -> _SourceAttempt:
        name = "Reddit OAuth"
        if not self.reddit_client_id or not self.reddit_client_secret:
            reason = "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set"
            print(f"{name} skipped ({reason}).")
            return _SourceAttempt(name=name, skipped=True, reason=reason)
        try:
            token_resp = requests.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(self.reddit_client_id, self.reddit_client_secret),
                data={"grant_type": "client_credentials"},
                headers=self._headers(), timeout=HTTP_TIMEOUT,
            )
            token_resp.raise_for_status()
            access_token = token_resp.json().get("access_token")
            if not access_token:
                raise RuntimeError(f"no access_token in response: {token_resp.text[:200]}")

            r = requests.get(
                "https://oauth.reddit.com/r/all/top",
                params={"t": "day", "limit": 10},
                headers=self._headers(Authorization=f"bearer {access_token}"),
                timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
            posts = r.json()["data"]["children"]
            titles = [p["data"]["title"] for p in posts
                      if not p["data"].get("over_18") and p["data"].get("title")]
            if not titles:
                raise RuntimeError("no Reddit OAuth titles")
            return _SourceAttempt(name=name, lines=titles)
        except Exception as e:
            print(f"{name} failed ({e}) -- skipping.")
            return _SourceAttempt(name=name, skipped=True, reason=str(e))

    def _pick_topic_with_gemini(self, research_context: str) -> str:
        response = self._client().models.generate_content(
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

    def _pick_topic_with_huggingface(self, research_context: str) -> str:
        client = InferenceClient(api_key=self.hf_token)
        completion = client.chat.completions.create(
            model="meta-llama/Llama-3.1-8B-Instruct",
            messages=[
                {"role": "system", "content": "Reply with ONLY one sentence naming the chosen topic."},
                {"role": "user", "content": (
                    f"{research_context}\n\n"
                    "Pick the SINGLE most compelling, story-worthy topic from the above "
                    "for a short narrative YouTube video. One sentence only."
                )},
            ],
            max_tokens=120, temperature=0.4,
        )
        topic = (completion.choices[0].message.content or "").strip()
        if not topic:
            raise RuntimeError("HF topic picker returned empty text")
        return topic

    def _research_topic(self) -> tuple[str, str, str, tuple[str, ...]]:
        """Runs every research source (Gemini grounded search first).
        Skipped sources are reported to Discord. Raises only if nothing
        usable remains. Returns (topic, research_context, topic_picker, used_sources)."""
        attempts: list[_SourceAttempt] = [
            self._fetch_gemini_grounded(),
            self._fetch_hacker_news(),
            self._fetch_wikipedia_current(),
            self._fetch_rss_feeds(),
            self._fetch_youtube_popular(),
            self._fetch_reddit_json(),
            self._fetch_reddit_oauth(),
        ]

        successful = [a for a in attempts if not a.skipped and a.lines]
        skipped = [a for a in attempts if a.skipped]

        if skipped:
            skip_lines = [f"**{a.name}**: {a.reason}" for a in skipped]
            try:
                send_research_skip_notification(self.discord_webhook_url, skip_lines)
                print(f"Discord notified about {len(skipped)} skipped research source(s).")
            except Exception as e:
                print(f"Discord skip notification failed ({e})")

        if not successful:
            names = ", ".join(a.name for a in attempts)
            raise RuntimeError(
                f"Trending research failed: every source was unavailable ({names}). "
                "Set VIDEO_TOPIC to override, or retry later."
            )

        sections = [f"### {a.name}\n{self._as_bullets(a.lines)}" for a in successful]
        research_context = (
            f"Live research gathered {date.today().isoformat()} UTC:\n\n" + "\n\n".join(sections)
        )

        try:
            topic = self._pick_topic_with_gemini(research_context)
            topic_picker = "Gemini"
        except Exception as e:
            if not self.hf_token:
                raise RuntimeError(f"Topic picking failed and no HF_TOKEN set: {e}") from e
            print(f"Gemini topic pick failed ({e}). Falling back to Hugging Face.")
            topic = self._pick_topic_with_huggingface(research_context)
            topic_picker = "Hugging Face"

        return topic, research_context, topic_picker, tuple(a.name for a in successful)

    # =========================================================
    # Auto-only: script generation (Gemini + HF fallback)
    # =========================================================

    def _format_research_block(self, research_context: str | None) -> str:
        if not research_context or not research_context.strip():
            return ""
        return f"\nUse this live research material when choosing the angle and details:\n{research_context.strip()}\n"

    def _build_script_prompt(self, topic: str, research_context: str | None) -> str:
        return SCRIPT_PROMPT_TEMPLATE.format(
            topic=topic, research_block=self._format_research_block(research_context),
        )

    def _hf_chat_json(self, client: InferenceClient, prompt: str) -> str:
        messages = [
            {"role": "system", "content": (
                "You output only valid JSON objects that match the requested "
                "schema. No markdown fences, no commentary."
            )},
            {"role": "user", "content": prompt},
        ]
        kwargs = {"model": "meta-llama/Llama-3.1-8B-Instruct", "messages": messages,
                  "max_tokens": 2048, "temperature": 0.4}
        try:
            completion = client.chat.completions.create(
                **kwargs,
                response_format={"type": "json_schema", "json_schema": {
                    "name": "youtube_script", "schema": SCRIPT_JSON_SCHEMA, "strict": True,
                }},
            )
        except Exception as e:
            print(f"HF json_schema response_format unsupported ({e}); trying json_object.")
            try:
                completion = client.chat.completions.create(**kwargs, response_format={"type": "json_object"})
            except Exception as e2:
                print(f"HF json_object response_format unsupported ({e2}); plain chat.")
                completion = client.chat.completions.create(**kwargs)
        return completion.choices[0].message.content

    def _generate_with_huggingface(self, topic: str, research_context: str | None) -> dict:
        client = InferenceClient(api_key=self.hf_token)
        prompt = self._build_script_prompt(topic, research_context) + "\n\nReturn ONLY the JSON object, no other text."

        last_error = None
        for attempt in range(1, MAX_HF_JSON_RETRIES + 1):
            try:
                content = self._hf_chat_json(client, prompt)
                return extract_json(content)
            except (ValueError, TypeError, IndexError, KeyError) as e:
                last_error = e
                print(f"HF JSON parse attempt {attempt}/{MAX_HF_JSON_RETRIES} failed: {e}")
                if attempt < MAX_HF_JSON_RETRIES:
                    time.sleep(2 * attempt)
        raise RuntimeError(
            f"Hugging Face fallback returned unparseable JSON after "
            f"{MAX_HF_JSON_RETRIES} attempts: {last_error}"
        ) from last_error

    def _generate_script_once(self, topic: str, research_context: str | None) -> tuple[dict, str]:
        prompt = self._build_script_prompt(topic, research_context)
        try:
            script = self.call_gemini_json(prompt, attempts=3, retry_wait=10)
            return script, "Gemini"
        except Exception as e:
            if not self.hf_token:
                raise
            print(f"Gemini unavailable after retries ({e}). Falling back to Hugging Face.")
            script = self._generate_with_huggingface(topic, research_context)
            return script, "Hugging Face"

    def _generate_script_with_fallback(self, topic: str, research_context: str | None) -> tuple[dict, str]:
        """Generates a script, then runs it through the quality gate.
        Below-threshold scripts get regenerated up to MAX_QUALITY_RETRIES
        times before the run fails outright."""
        last_score, last_breakdown, last_issues = None, None, None

        for attempt in range(1, MAX_QUALITY_RETRIES + 1):
            script, provider = self._generate_script_once(topic, research_context)
            score, breakdown, issues = score_script(script)
            print(f"Quality gate attempt {attempt}/{MAX_QUALITY_RETRIES}: {score}/100 {breakdown} (via {provider})")

            if score >= QUALITY_THRESHOLD:
                return script, provider

            print(f"Below threshold ({QUALITY_THRESHOLD}). Issues: {issues}")
            last_score, last_breakdown, last_issues = score, breakdown, issues

        raise RuntimeError(
            f"Script failed the quality gate after {MAX_QUALITY_RETRIES} attempts. "
            f"Last score: {last_score}/100 {last_breakdown}. Issues: {last_issues}"
        )

    # =========================================================
    # Auto-only: stock visuals + background music
    # =========================================================

    def fetch_stock_video(self, query: str, out_path: Path) -> None:
        """Pexels video search for vertical Shorts/TikTok. Prefers
        portrait clips, falls back to any orientation if portrait is
        empty, then picks the file closest to 1080px wide."""
        headers = {"Authorization": self.pexels_api_key}
        candidates = [query]
        words = query.split()
        if len(words) > 3:
            candidates.append(" ".join(words[:3]))

        last_error = None
        for q in candidates:
            for orientation in ("portrait", "square", None):
                try:
                    params = {"query": q, "per_page": 5}
                    if orientation:
                        params["orientation"] = orientation

                    r = requests.get("https://api.pexels.com/videos/search",
                                      params=params, headers=headers, timeout=30)
                    r.raise_for_status()
                    videos = r.json().get("videos") or []
                    if not videos:
                        label = orientation or "any"
                        last_error = RuntimeError(f"No Pexels video results for query: {q} ({label})")
                        continue

                    for video in videos:
                        files = [f for f in video.get("video_files", [])
                                 if f.get("file_type") == "video/mp4" and f.get("link")]
                        if not files:
                            continue

                        best = min(files, key=lambda f: abs((f.get("width") or 0) - 1080))
                        download = requests.get(best["link"], timeout=120)
                        download.raise_for_status()
                        out_path.write_bytes(download.content)
                        if q != query or orientation != "portrait":
                            print(f"Pexels used query={q!r} orientation={orientation or 'any'} "
                                  f"(original query={query!r})")
                        return

                    last_error = RuntimeError(f"No mp4 files in Pexels results for query: {q}")
                except Exception as e:
                    last_error = e
                    print(f"Pexels fetch failed for {q!r} ({orientation or 'any'}): {e}")

        raise RuntimeError(f"Pexels video fetch failed for {query!r}: {last_error}")

    def mix_background_music(self, video_path: Path, out_path: Path, music_volume: float = 0.12) -> None:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-stream_loop", "-1", "-i", str(self.music_path),
                "-filter_complex",
                f"[1:a]volume={music_volume}[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[a]",
                "-map", "0:v", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", "-shortest",
                str(out_path),
            ],
            check=True,
        )