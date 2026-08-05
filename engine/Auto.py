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

# ~150 wpm spoken → 150 words ≈ ~60s.
MAX_STORY_WORDS = 150

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
- Segment 1 has to hook immediately, no "so this happened" or "let me
  explain." Drop straight into the situation or the twist.
- Short, punchy, spoken-out-loud sentences. No em dashes, no "it's not
  just X, it's Y," no corporate or AI-sounding words like leverage,
  delve, testament, robust.
- The FULL spoken story must be under 60 seconds (~150 words total
  across every line). Prefer 5 to 8 short lines. Cut ruthlessly.
- Lean into whichever side of the conflict the details make sharper.
  Don't soften it into "both sides have a point." Let the dialogue
  carry the edge, don't add narrator commentary telling people who's
  right.
- End on the actual stakes or tension, not a flat summary.
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
    ... (5 to 8 segments total)
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
            return script

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
        speaker_refs = self.pick_speaker_refs(script)
        if not speaker_refs.get("op"):
            raise RuntimeError("No OP voice reference resolved — check assets/auto/male|female")
        clip_paths = []
        # Overlap Pexels download with TTS — network wait is free during CPU TTS.
        with ThreadPoolExecutor(max_workers=1) as pool:
            for i, seg in enumerate(script["segments"]):
                speaker = seg.get("speaker", "op")
                ref = speaker_refs.get(speaker) or speaker_refs["op"]
                narration = self.tts_text(seg["narration"], speaker)
                print(f"Segment {i} ({speaker}): {narration[:60]}...")
                audio_path = self.workdir / f"seg_{i}.wav"
                video_path = self.workdir / f"seg_{i}_source.mp4"
                clip_path = self.workdir / f"seg_{i}.mp4"

                fetch = pool.submit(
                    self.fetch_stock_video, seg["image_query"], video_path
                )
                self.voice.synthesize_speech(narration, audio_path, ref)
                dur = self.voice.wav_duration_seconds(audio_path)
                print(f"  TTS {dur:.2f}s → {audio_path.name}")
                fetch.result()
                self.assemble.build_segment_clip(
                    video_path, audio_path, clip_path, narration
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

    def gender_voice_dir(self, gender: str) -> Path:
        return MALE_VOICE_DIR if gender == "male" else FEMALE_VOICE_DIR

    def list_gender_mp3s(self, gender: str) -> list[Path]:
        folder = self.gender_voice_dir(gender)
        if not folder.is_dir():
            return []
        return sorted(folder.glob("*.mp3"))

    def fallback_reference(self) -> Path:
        """Single legacy voice_reference when a gender folder is empty."""
        if self.reference_clip is None:
            self.reference_clip = self.voice.resolve_reference_clip(
                self.voice_wav, self.voice_mp3, self.voice_converted
            )
        return self.reference_clip

    def resolve_voice_mp3s(self, mp3s: list[Path]) -> list[Path]:
        return [self.voice.resolve_mp3_by_stem(p, VOICE_CACHE_DIR) for p in mp3s]

    def pick_gender_clips(self, gender: str, n: int) -> list[Path]:
        mp3s = self.list_gender_mp3s(gender)
        if len(mp3s) >= n:
            chosen = random.sample(mp3s, n) if n > 1 else [random.choice(mp3s)]
            return self.resolve_voice_mp3s(chosen)
        if not mp3s:
            print(f"No {gender} voices in {self.gender_voice_dir(gender)}; using fallback.")
            return [self.fallback_reference()] * n
        # Not enough distinct files — use what we have, pad with fallback.
        chosen = random.sample(mp3s, len(mp3s))
        wavs = self.resolve_voice_mp3s(chosen)
        while len(wavs) < n:
            wavs.append(self.fallback_reference())
        return wavs

    def pick_speaker_refs(self, script: dict) -> dict[str, Path]:
        """Pick TTS refs from story genders. Dual-voice by default;
        single_voice (oneShot) uses one random clip for every speaker."""
        op_gender = script.get("op_gender") or "male"
        if op_gender not in ("male", "female"):
            op_gender = "male"
        has_other = any(seg.get("speaker") == "other" for seg in script["segments"])
        other_gender = script.get("other_gender")
        if has_other and other_gender not in ("male", "female"):
            other_gender = "female" if op_gender == "male" else "male"

        if self.single_voice:
            # Narrator gender = OP; one random male/female clip for the whole video.
            wav = self.pick_gender_clips(op_gender, 1)[0]
            print(f"Voices (single): {wav.name} ({op_gender})")
            refs = {"op": wav}
            if has_other:
                refs["other"] = wav
            return refs

        if has_other and other_gender == op_gender:
            op_wav, other_wav = self.pick_gender_clips(op_gender, 2)
            print(f"Voices: op={op_wav.name} other={other_wav.name} ({op_gender})")
            return {"op": op_wav, "other": other_wav}

        op_wav = self.pick_gender_clips(op_gender, 1)[0]
        refs = {"op": op_wav}
        if has_other:
            other_wav = self.pick_gender_clips(other_gender, 1)[0]
            refs["other"] = other_wav
            print(
                f"Voices: op={op_wav.name} ({op_gender}) "
                f"other={other_wav.name} ({other_gender})"
            )
        else:
            print(f"Voices: op={op_wav.name} ({op_gender}) (no OTHER lines)")
        return refs

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
