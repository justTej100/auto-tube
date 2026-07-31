"""Auto: fully autonomous channel production. Researches (or takes a
given) topic, writes a freeform narrative script with Gemini + Hugging
Face fallback, pulls Pexels stock footage per segment, assembles with
background music, uploads, notifies. No human input needed once
configured."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests
from huggingface_hub import InferenceClient

from engine.Channel import Channel
from engine.Trending import Trending

MAX_QUALITY_RETRIES = 3
MAX_HF_JSON_RETRIES = 3

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
{reddit_story_block}
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


REDDIT_STORY_BLOCK = """
This topic is a Reddit story. Write the video as a narrative ABOUT that
Reddit post -- what the post says happened, why it blew up, the human
angle -- not a generic essay on a loosely related theme. Stay faithful
to the Reddit material in the research above.
"""


@dataclass
class AutoContext:
    """What prepare() gathers before scripting: the topic plus whatever
    research backed it (empty if VIDEO_TOPIC was set manually)."""
    topic: str
    research_context: str | None
    topic_picker: str | None
    research_sources: tuple[str, ...]
    script_provider: str | None = None
    from_reddit: bool = False


class Auto(Channel):
    def __init__(
        self,
        gemini_api_key: str,
        hf_token: str | None,
        pexels_api_key: str,
        discord_webhook_url: str,
        google_client_id: str,
        google_client_secret: str,
        google_refresh_token: str,
        drive_folder_id: str,
        video_topic: str | None = None,
        youtube_api_key: str | None = None,
        reddit_client_id: str | None = None,
        reddit_client_secret: str | None = None,
        workdir: Path = Path("build/auto"),
        voice_wav: Path = Path("assets/auto/voice_reference.wav"),
        voice_mp3: Path = Path("assets/auto/voice_reference.mp3"),
        voice_converted: Path = Path("build/auto/voice_reference_converted.wav"),
        music_path: Path = Path("assets/auto/background_music.mp3"),
    ):
        super().__init__(
            gemini_api_key,
            hf_token,
            workdir,
            voice_wav,
            voice_mp3,
            voice_converted,
            google_client_id,
            google_client_secret,
            google_refresh_token,
            discord_webhook_url,
            discord_username="auto",
        )
        self.pexels_api_key = pexels_api_key
        self.drive_folder_id = drive_folder_id
        self.video_topic = video_topic
        self.music_path = music_path
        self.trending = Trending(
            gemini_api_key,
            discord=self.discord,
            hf_token=hf_token,
            youtube_api_key=youtube_api_key,
            reddit_client_id=reddit_client_id,
            reddit_client_secret=reddit_client_secret,
        )

    # =========================================================
    # Template hooks
    # =========================================================

    def prepare(self) -> AutoContext:
        if self.video_topic:
            print(f"Using provided topic: {self.video_topic}")
            return AutoContext(
                topic=self.video_topic,
                research_context=None,
                topic_picker=None,
                research_sources=(),
            )

        print(
            "Researching today's trending topic "
            "(Reddit first, then YouTube, news, Gemini grounded)..."
        )
        result = self.trending.research()
        print(f"Topic selected via {result.topic_picker}: {result.topic}")
        if result.from_reddit:
            print("Topic locked to a Reddit story.")
        return AutoContext(
            topic=result.topic,
            research_context=result.research_context,
            topic_picker=result.topic_picker,
            research_sources=result.used_sources,
            from_reddit=result.from_reddit,
        )

    def generate_script(self, context: AutoContext) -> dict:
        def once() -> dict:
            script, provider = self.generate_script_once(
                context.topic,
                context.research_context,
                from_reddit=context.from_reddit,
            )
            context.script_provider = provider
            return script

        script = self.generate_until_quality(once, max_attempts=MAX_QUALITY_RETRIES)
        print(f"Script generated via {context.script_provider}")
        return script

    def render_segments(self, script: dict, context: AutoContext) -> list[Path]:
        clip_paths = []
        for i, seg in enumerate(script["segments"]):
            print(f"Segment {i}: {seg['narration'][:60]}...")
            audio_path = self.workdir / f"seg_{i}.wav"
            video_path = self.workdir / f"seg_{i}_source.mp4"
            clip_path = self.workdir / f"seg_{i}.mp4"

            self.voice.synthesize_speech(seg["narration"], audio_path, self.reference_clip)
            self.fetch_stock_video(seg["image_query"], video_path)
            self.assemble.build_segment_clip(
                video_path, audio_path, clip_path, seg["narration"]
            )
            clip_paths.append(clip_path)
        return clip_paths

    def finalize_assembly(self, clips: list[Path], script: dict, context: AutoContext) -> Path:
        final_path = self.workdir / "final.mp4"
        self.assemble.concat_clips(clips, final_path)
        print(f"Video assembled: {final_path}")

        if self.music_path.exists():
            mixed_path = self.workdir / "final_with_music.mp4"
            self.assemble.mix_background_music(final_path, self.music_path, mixed_path)
            final_path = mixed_path
            print("Background music mixed in.")
        else:
            print(f"No {self.music_path} found -- skipping music (optional).")

        return final_path

    def deliver(self, final_path: Path, script: dict, context: AutoContext) -> str:
        filename = f"{date.today().isoformat()} - {script['title']}.mp4"
        drive_link = self.drive.upload_file(self.drive_folder_id, final_path, filename)
        print(f"Uploaded to Drive: {drive_link}")

        self.discord.send_review(
            script["title"],
            drive_link,
            topic=context.topic,
            topic_picker=context.topic_picker,
            script_provider=context.script_provider,
            research_sources=list(context.research_sources) or None,
        )
        print("Discord notification sent.")
        return drive_link

    # =========================================================
    # Auto-only: script generation (Gemini + HF fallback)
    # =========================================================

    def format_research_block(self, research_context: str | None) -> str:
        if not research_context or not research_context.strip():
            return ""
        return (
            "\nUse this live research material when choosing the angle and details:\n"
            f"{research_context.strip()}\n"
        )

    def build_script_prompt(
        self,
        topic: str,
        research_context: str | None,
        *,
        from_reddit: bool = False,
    ) -> str:
        return SCRIPT_PROMPT_TEMPLATE.format(
            topic=topic,
            research_block=self.format_research_block(research_context),
            reddit_story_block=REDDIT_STORY_BLOCK if from_reddit else "",
        )

    def hf_chat_json(self, client: InferenceClient, prompt: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "You output only valid JSON objects that match the requested "
                    "schema. No markdown fences, no commentary."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        kwargs = {
            "model": "meta-llama/Llama-3.1-8B-Instruct",
            "messages": messages,
            "max_tokens": 2048,
            "temperature": 0.4,
        }
        try:
            completion = client.chat.completions.create(
                **kwargs,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "youtube_script",
                        "schema": SCRIPT_JSON_SCHEMA,
                        "strict": True,
                    },
                },
            )
        except Exception as e:
            print(f"HF json_schema response_format unsupported ({e}); trying json_object.")
            try:
                completion = client.chat.completions.create(
                    **kwargs, response_format={"type": "json_object"}
                )
            except Exception as e2:
                print(f"HF json_object response_format unsupported ({e2}); plain chat.")
                completion = client.chat.completions.create(**kwargs)
        return completion.choices[0].message.content

    def generate_with_huggingface(
        self,
        topic: str,
        research_context: str | None,
        *,
        from_reddit: bool = False,
    ) -> dict:
        client = InferenceClient(api_key=self.hf_token)
        prompt = (
            self.build_script_prompt(topic, research_context, from_reddit=from_reddit)
            + "\n\nReturn ONLY the JSON object, no other text."
        )

        last_error = None
        for attempt in range(1, MAX_HF_JSON_RETRIES + 1):
            try:
                content = self.hf_chat_json(client, prompt)
                return self.extract_json(content)
            except (ValueError, TypeError, IndexError, KeyError) as e:
                last_error = e
                print(f"HF JSON parse attempt {attempt}/{MAX_HF_JSON_RETRIES} failed: {e}")
                if attempt < MAX_HF_JSON_RETRIES:
                    time.sleep(2 * attempt)
        raise RuntimeError(
            f"Hugging Face fallback returned unparseable JSON after "
            f"{MAX_HF_JSON_RETRIES} attempts: {last_error}"
        ) from last_error

    def generate_script_once(
        self,
        topic: str,
        research_context: str | None,
        *,
        from_reddit: bool = False,
    ) -> tuple[dict, str]:
        prompt = self.build_script_prompt(
            topic, research_context, from_reddit=from_reddit
        )
        try:
            script = self.call_gemini_json(prompt, attempts=3, retry_wait=10)
            return script, "Gemini"
        except Exception as e:
            if not self.hf_token:
                raise
            print(f"Gemini unavailable after retries ({e}). Falling back to Hugging Face.")
            script = self.generate_with_huggingface(
                topic, research_context, from_reddit=from_reddit
            )
            return script, "Hugging Face"

    # =========================================================
    # Auto-only: stock visuals
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

                    r = requests.get(
                        "https://api.pexels.com/videos/search",
                        params=params,
                        headers=headers,
                        timeout=30,
                    )
                    r.raise_for_status()
                    videos = r.json().get("videos") or []
                    if not videos:
                        label = orientation or "any"
                        last_error = RuntimeError(
                            f"No Pexels video results for query: {q} ({label})"
                        )
                        continue

                    for video in videos:
                        files = [
                            f
                            for f in video.get("video_files", [])
                            if f.get("file_type") == "video/mp4" and f.get("link")
                        ]
                        if not files:
                            continue

                        best = min(files, key=lambda f: abs((f.get("width") or 0) - 1080))
                        download = requests.get(best["link"], timeout=120)
                        download.raise_for_status()
                        out_path.write_bytes(download.content)
                        if q != query or orientation != "portrait":
                            print(
                                f"Pexels used query={q!r} orientation={orientation or 'any'} "
                                f"(original query={query!r})"
                            )
                        return

                    last_error = RuntimeError(f"No mp4 files in Pexels results for query: {q}")
                except Exception as e:
                    last_error = e
                    print(f"Pexels fetch failed for {q!r} ({orientation or 'any'}): {e}")

        raise RuntimeError(f"Pexels video fetch failed for {query!r}: {last_error}")
