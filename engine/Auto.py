"""Auto: fully autonomous channel production. Researches (or takes a
given) topic, writes a freeform narrative script with Gemini + Hugging
Face fallback, pulls Pexels stock footage per segment, assembles with
background music, uploads, notifies. No human input needed once
configured."""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import requests
from huggingface_hub import InferenceClient

from engine.Channel import Channel
from engine.Reddit import Reddit

MAX_QUALITY_RETRIES = 3
MAX_HF_JSON_RETRIES = 3

MALE_VOICE_DIR = Path("assets/auto/male")
FEMALE_VOICE_DIR = Path("assets/auto/female")
VOICE_CACHE_DIR = Path("build/auto/voices")
FALLBACK_VOICE_MP3 = Path("assets/auto/voice_reference.mp3")

SCRIPT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "op_gender": {"type": "string", "enum": ["male", "female"]},
        "other_gender": {
            "anyOf": [
                {"type": "string", "enum": ["male", "female"]},
                {"type": "null"},
            ]
        },
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string", "enum": ["op", "other"]},
                    "narration": {"type": "string"},
                    "image_query": {"type": "string"},
                },
                "required": ["speaker", "narration", "image_query"],
                "additionalProperties": False,
            },
            "minItems": 5,
            "maxItems": 8,
        },
    },
    "required": ["title", "description", "op_gender", "other_gender", "segments"],
    "additionalProperties": False,
}

SCRIPT_PROMPT_TEMPLATE = """Write a short-video script based on the material below,
using two voices: OP and OTHER.

Topic: {topic}
{research_block}
OP is the poster. OP narrates the entire story in first person ("I",
"my"), and also speaks their own quoted lines. OTHER is the other
person in the conflict (spouse, coworker, friend) and speaks ONLY when
directly quoted.

Rules:

- Every OTHER line must come right after an OP line that ends in an
  attribution with no closing punctuation, like "she said," or "he told
  me," so OTHER picks up mid-sentence. Never give OTHER a line without
  that attribution right before it.
- Only quote OTHER for lines that are actually in the material or clearly
  implied as something they said. Don't invent dialogue. If there are
  no clear quotes, use zero OTHER lines.
- Use 2 to 3 OTHER lines max. Never more, never back to back.
- OP stays first person the whole time. Never slips into "he" or "the
  poster."
- Open and close on OP. Never start or end on an OTHER line.
- Segment 1 has to hook immediately -- a surprising claim, a twist, or
  dropping straight into the situation. No "so this happened," "let me
  explain," "Did you know," or "Today we're talking about."
- Short, punchy, spoken-out-loud sentences. No em dashes, no "it's not
  just X, it's Y," no corporate or AI-sounding words like leverage,
  delve, testament, robust.
- Lean into whichever side of the conflict the details make sharper.
  Don't soften it into "both sides have a point." Let the dialogue
  carry the edge, don't add narrator commentary telling people who's
  right.
- End on the actual stakes or tension, not a flat summary.
- 6 to 10 lines total.
- Never say "Reddit", "Reddit story", "post blew up", or similar in
  narration, title, or description. Tell it as a personal story.
- Never put the words OP or OTHER in any narration text. Those labels
  are JSON metadata only.
- Infer op_gender and other_gender (male/female) from the story
  context. If there are zero OTHER lines, set other_gender to null.
- For each segment's image_query, write a SPECIFIC stock-VIDEO search
  query (3-6 concrete words) that a Pexels video search can match --
  not abstract concepts. Prefer filmable vertical/portrait scenes with
  clear subjects and action.

Return JSON in this exact shape:
{{
  "title": "...",
  "description": "...",
  "op_gender": "male",
  "other_gender": "female",
  "segments": [
    {{"speaker": "op", "narration": "...", "image_query": "specific vivid video search, 3-6 words"}},
    {{"speaker": "other", "narration": "...", "image_query": "..."}},
    ... (6 to 10 segments total)
  ]
}}

JSON rules: use double quotes only, no trailing commas, no comments,
and escape any double quotes inside string values as \\". """


class AutoContext:
    """What prepare() gathers before scripting: the topic plus whatever
    research backed it (empty if VIDEO_TOPIC was set manually)."""

    def __init__(
        self,
        topic: str,
        research_context: str | None,
        topic_picker: str | None,
        research_sources: tuple[str, ...] = (),
        research_findings: tuple[tuple[str, tuple[str, ...]], ...] = (),
        script_provider: str | None = None,
    ) -> None:
        self.topic = topic
        self.research_context = research_context
        self.topic_picker = topic_picker
        self.research_sources = research_sources
        self.research_findings = research_findings
        self.script_provider = script_provider


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
        reddit_client_id: str | None = None,
        reddit_client_secret: str | None = None,
        workdir: Path = Path("build/auto"),
        voice_wav: Path = Path("assets/auto/voice_reference.wav"),
        voice_mp3: Path = Path("assets/auto/voice_reference.mp3"),
        voice_converted: Path = Path("build/auto/voice_reference_converted.wav"),
        music_path: Path = Path("assets/auto/background_music.mp3"),
        single_voice: bool = False,
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
        self.single_voice = single_voice
        self._used_pexels_ids: set[int] = set()
        self.trending = Reddit(
            gemini_api_key,
            discord=self.discord,
            reddit_client_id=reddit_client_id or "",
            reddit_client_secret=reddit_client_secret or "",
            hf_token=hf_token,
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

        print("Researching today's top posts...")
        result = self.trending.research()
        print(f"Topic selected via {result.topic_picker}: {result.topic}")
        return AutoContext(
            topic=result.topic,
            research_context=result.research_context,
            topic_picker=result.topic_picker,
            research_sources=result.used_sources,
            research_findings=result.research_findings,
        )

    def generate_script(self, context: AutoContext) -> dict:
        def once() -> dict:
            script, provider = self.generate_script_once(
                context.topic,
                context.research_context,
            )
            script = self.normalize_script(script)
            words = self.story_word_count(script)
            print(f"Script word count: {words} (max {MAX_STORY_WORDS} ≈ 60s)")
            if words > MAX_STORY_WORDS:
                raise RuntimeError(
                    f"Script too long for a 60s video ({words} words > {MAX_STORY_WORDS})"
                )
            context.script_provider = provider
            return self.normalize_script(script)

        script = self.generate_until_quality(once, max_attempts=MAX_QUALITY_RETRIES)
        print(f"Script generated via {context.script_provider}")
        return script

    @staticmethod
    def story_word_count(script: dict) -> int:
        return sum(len((seg.get("narration") or "").split()) for seg in script["segments"])

    @staticmethod
    def normalize_script(script: dict) -> dict:
        """Tolerate Gemini casing / missing fields so publish doesn't die on voice pick."""
        gender_aliases = {
            "male": "male",
            "man": "male",
            "m": "male",
            "female": "female",
            "woman": "female",
            "f": "female",
        }

        def gender(value) -> str | None:
            if value is None or value == "":
                return None
            return gender_aliases.get(str(value).strip().lower())

        script["op_gender"] = gender(script.get("op_gender")) or "male"
        script["other_gender"] = gender(script.get("other_gender"))

        segments = script.get("segments") or []
        if not segments:
            raise RuntimeError("Script has no segments")
        for seg in segments:
            sp = str(seg.get("speaker") or "op").strip().lower()
            seg["speaker"] = "other" if sp in ("other", "quote", "quoted") else "op"
            if not seg.get("narration"):
                raise RuntimeError("Script segment missing narration")
            if not seg.get("image_query"):
                seg["image_query"] = "person talking closeup"
        script["segments"] = segments
        return script

    def render_segments(self, script: dict, context: AutoContext) -> list[Path]:
        self._used_pexels_ids.clear()
        voices = self.pick_speaker_voices(script)
        clip_paths = []
        for i, seg in enumerate(script["segments"]):
            speaker = seg.get("speaker", "op")
            ref = voices.get(speaker) or voices["op"]
            print(f"Segment {i} [{speaker}]: {seg['narration'][:60]}...")
            audio_path = self.workdir / f"seg_{i}.wav"
            video_path = self.workdir / f"seg_{i}_source.mp4"
            clip_path = self.workdir / f"seg_{i}.mp4"

            self.voice.synthesize_speech(seg["narration"], audio_path, ref)
            self.fetch_stock_video(seg["image_query"], video_path)
            self.assemble.build_segment_clip(
                video_path, audio_path, clip_path, seg["narration"]
            )
            clip_paths.append(clip_path)
        return clip_paths

    @staticmethod
    def tts_text(narration: str, speaker: str) -> str:
        """Strip wrapping quotes on OTHER lines so TTS doesn't vocalize them."""
        text = narration.strip()
        if speaker == "other" and len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
            text = text[1:-1].strip()
        return text

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
            research_findings=context.research_findings or None,
        )
        print("Discord notification sent.")
        return drive_link

    # =========================================================
    # Auto-only: script generation (Gemini + HF fallback)
    # =========================================================

    def setup_voice(self) -> None:
        """Prefer gender folders; keep legacy voice_reference as fallback only."""
        has_gender = bool(self.list_gender_mp3s("male") or self.list_gender_mp3s("female"))
        try:
            self.reference_clip = self.voice.resolve_reference_clip(
                self.voice_wav, self.voice_mp3, self.voice_converted
            )
        except FileNotFoundError:
            if not has_gender:
                raise
            self.reference_clip = None
            print("No legacy voice_reference; using assets/auto/male|female only.")

    def format_research_block(self, research_context: str | None) -> str:
        if not research_context or not research_context.strip():
            return ""
        return (
            "\nUse this source material when choosing the angle and details:\n"
            f"{research_context.strip()}\n"
        )

    def build_script_prompt(
        self,
        topic: str,
        research_context: str | None,
    ) -> str:
        return SCRIPT_PROMPT_TEMPLATE.format(
            topic=topic,
            research_block=self.format_research_block(research_context),
        )

    @staticmethod
    def normalize_script(script: dict) -> dict:
        """Coerce Gemini gender/speaker labels to the schema we render with."""
        gender_aliases = {
            "male": "male",
            "man": "male",
            "m": "male",
            "female": "female",
            "woman": "female",
            "f": "female",
        }

        def gender(value) -> str | None:
            if value is None or value == "":
                return None
            return gender_aliases.get(str(value).strip().lower())

        script["op_gender"] = gender(script.get("op_gender")) or "male"
        script["other_gender"] = gender(script.get("other_gender"))
        for seg in script["segments"]:
            sp = str(seg.get("speaker") or "op").strip().lower()
            seg["speaker"] = "other" if sp in ("other", "quote", "quoted") else "op"
        if not any(seg["speaker"] == "other" for seg in script["segments"]):
            script["other_gender"] = None
        return script

    def gender_mp3s(self, gender: str) -> list[Path]:
        folder = MALE_VOICE_DIR if gender == "male" else FEMALE_VOICE_DIR
        return sorted(folder.glob("*.mp3"))

    def resolve_voice_mp3(self, mp3: Path) -> Path:
        return self.voice.resolve_mp3_by_stem(mp3, VOICE_CACHE_DIR)

    def fallback_voice(self) -> Path:
        if self.reference_clip is not None:
            return self.reference_clip
        if FALLBACK_VOICE_MP3.exists():
            return self.resolve_voice_mp3(FALLBACK_VOICE_MP3)
        raise FileNotFoundError(
            f"No voice clips in {MALE_VOICE_DIR} / {FEMALE_VOICE_DIR} "
            f"and missing fallback {FALLBACK_VOICE_MP3}"
        )

    def pick_from_gender(self, gender: str, *, exclude: Path | None = None) -> Path:
        pool = [p for p in self.gender_mp3s(gender) if exclude is None or p != exclude]
        if not pool:
            pool = self.gender_mp3s(gender)
        if not pool:
            return self.fallback_voice()
        return self.resolve_voice_mp3(random.choice(pool))

    def pick_speaker_voices(self, script: dict) -> dict[str, Path]:
        """Map speaker → wav reference. Same-gender OP/OTHER get two different mp3s."""
        op_g = script["op_gender"]
        other_g = script.get("other_gender")
        needs_other = other_g is not None and any(
            seg.get("speaker") == "other" for seg in script["segments"]
        )

        if needs_other and other_g == op_g:
            pool = self.gender_mp3s(op_g)
            if len(pool) >= 2:
                a, b = random.sample(pool, 2)
                voices = {"op": self.resolve_voice_mp3(a), "other": self.resolve_voice_mp3(b)}
            elif len(pool) == 1:
                only = self.resolve_voice_mp3(pool[0])
                voices = {"op": only, "other": only}
            else:
                fb = self.fallback_voice()
                voices = {"op": fb, "other": fb}
        else:
            voices = {"op": self.pick_from_gender(op_g)}
            if needs_other:
                voices["other"] = self.pick_from_gender(other_g)
            else:
                voices["other"] = voices["op"]

        print(
            f"Voices: op={voices['op'].name} ({op_g})"
            + (f", other={voices['other'].name} ({other_g})" if needs_other else "")
        )
        return voices

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
    ) -> dict:
        client = InferenceClient(api_key=self.hf_token)
        prompt = (
            self.build_script_prompt(topic, research_context)
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
    ) -> tuple[dict, str]:
        prompt = self.build_script_prompt(topic, research_context)
        try:
            script = self.call_gemini_json(prompt, attempts=3, retry_wait=10)
            return script, "Gemini"
        except Exception as e:
            if not self.hf_token:
                raise
            print(f"Gemini unavailable after retries ({e}). Falling back to Hugging Face.")
            script = self.generate_with_huggingface(topic, research_context)
            return script, "Hugging Face"

    # =========================================================
    # Auto-only: stock visuals
    # =========================================================

    def fetch_stock_video(self, query: str, out_path: Path) -> None:
        """Pexels video search for vertical Shorts/TikTok. Prefers
        portrait clips, falls back to any orientation if portrait is
        empty, then picks the file closest to 1080px wide. Skips video
        IDs already used earlier in this render so clips don't repeat."""
        headers = {"Authorization": self.pexels_api_key}
        candidates = [query]
        words = query.split()
        if len(words) > 3:
            candidates.append(" ".join(words[:3]))

        last_error = None
        for q in candidates:
            for orientation in ("portrait", "square", None):
                try:
                    params = {"query": q, "per_page": 15}
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
                        video_id = video.get("id")
                        if video_id is not None and video_id in self._used_pexels_ids:
                            continue

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
                        if video_id is not None:
                            self._used_pexels_ids.add(video_id)
                        if q != query or orientation != "portrait":
                            print(
                                f"Pexels used query={q!r} orientation={orientation or 'any'} "
                                f"(original query={query!r})"
                            )
                        return

                    last_error = RuntimeError(
                        f"No unused mp4 Pexels results for query: {q}"
                    )
                except Exception as e:
                    last_error = e
                    print(f"Pexels fetch failed for {q!r} ({orientation or 'any'}): {e}")

        raise RuntimeError(f"Pexels video fetch failed for {query!r}: {last_error}")
