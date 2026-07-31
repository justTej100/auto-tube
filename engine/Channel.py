"""Base class for a channel's daily production run. Both Auto (fully
autonomous) and RankedNiche (human-curated countdown) subclass this.

run() is the template method: the sequence never changes, only what each
hook does per channel. Shared services are composed onto every Channel:
DiscordNotifier, QualityControl, Voice, Assemble, Drive."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path

from google import genai
from google.genai import types

from engine.Assemble import Assemble
from engine.DiscordNotify import DiscordMessage, DiscordNotifier
from engine.Drive import Drive
from engine.QualityControl import QualityControl
from engine.Voice import Voice


class Channel:
    def __init__(
        self,
        gemini_api_key: str,
        hf_token: str | None,
        workdir: Path,
        voice_wav: Path,
        voice_mp3: Path,
        voice_converted: Path,
        google_client_id: str,
        google_client_secret: str,
        google_refresh_token: str,
        discord_webhook_url: str,
        discord_username: str | None = None,
    ):
        self.gemini_api_key = gemini_api_key
        self.hf_token = hf_token
        self.workdir = workdir
        self.voice_wav = voice_wav
        self.voice_mp3 = voice_mp3
        self.voice_converted = voice_converted
        self.discord_username = discord_username

        self.discord = DiscordNotifier(discord_webhook_url, default_username=discord_username)
        self.qc = QualityControl()
        self.voice = Voice()
        self.assemble = Assemble(workdir, self.voice)
        self.drive = Drive(google_client_id, google_client_secret, google_refresh_token)

        self.reference_clip: Path | None = None
        self.gemini_client: genai.Client | None = None

    # =========================================================
    # Template method -- fixed sequence, hooks do the real work
    # =========================================================

    def run(self) -> str | None:
        self.workdir.mkdir(parents=True, exist_ok=True)

        try:
            context = self.prepare()
            if context is None:
                return None

            self.setup_voice()
            script = self.generate_script(context)
            clips = self.render_segments(script, context)
            final_path = self.finalize_assembly(clips, script, context)
            result = self.deliver(final_path, script, context)
            self.cleanup(context)
            return result
        except Exception as e:
            try:
                self.discord.send_error(e, username=self.discord_username)
            except Exception as notify_err:
                print(f"Also failed to notify Discord about the failure: {notify_err}")
            raise

    # ---- hooks: every subclass must implement these ----

    def prepare(self):
        raise NotImplementedError

    def generate_script(self, context) -> dict:
        raise NotImplementedError

    def render_segments(self, script: dict, context) -> list[Path]:
        """TTS + captioned clips. Call self.voice / self.assemble."""
        raise NotImplementedError

    def finalize_assembly(self, clips: list[Path], script: dict, context) -> Path:
        raise NotImplementedError

    def deliver(self, final_path: Path, script: dict, context) -> str:
        raise NotImplementedError

    # ---- hooks with defaults ----

    def setup_voice(self) -> None:
        self.reference_clip = self.voice.resolve_reference_clip(
            self.voice_wav, self.voice_mp3, self.voice_converted
        )

    def cleanup(self, context) -> None:
        pass

    # =========================================================
    # Shared, concrete: LLM + JSON repair
    # =========================================================

    def get_gemini_client(self) -> genai.Client:
        if self.gemini_client is None:
            self.gemini_client = genai.Client(api_key=self.gemini_api_key)
        return self.gemini_client

    def call_gemini_json(self, prompt: str, attempts: int = 3, retry_wait: int = 10) -> dict:
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                resp = self.get_gemini_client().models.generate_content(
                    model="gemini-3-flash-preview",
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
                return json.loads(resp.text)
            except Exception as e:
                last_error = e
                print(f"Gemini attempt {attempt}/{attempts} failed: {e}")
                if attempt < attempts:
                    wait = retry_wait * attempt
                    print(f"Retrying in {wait}s...")
                    time.sleep(wait)
        raise last_error

    def repair_json(self, blob: str) -> str:
        """Fix common LLM JSON mistakes that otherwise fail json.loads."""
        blob = blob.replace("\u201c", '"').replace("\u201d", '"')
        blob = blob.replace("\u2018", "'").replace("\u2019", "'")
        blob = re.sub(r",\s*([}\]])", r"\1", blob)
        return blob

    def extract_json(self, text: str | None) -> dict:
        """Strip markdown fences / commentary around a JSON object (HF path)."""
        if not text or not text.strip():
            raise ValueError("Model returned empty content; expected a JSON object")

        text = text.strip()
        if text.startswith("```"):
            parts = text.split("```")
            text = parts[1] if len(parts) > 1 else text[3:]
            text = text.lstrip()
            if text.lower().startswith("json"):
                text = text[4:]

        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < 0 or end <= start:
            raise ValueError(f"No JSON object found in model response: {text[:200]!r}")

        blob = self.repair_json(text[start : end + 1])
        try:
            return json.loads(blob)
        except json.JSONDecodeError as e:
            snippet_start = max(0, e.pos - 60)
            snippet = blob[snippet_start : e.pos + 60]
            raise json.JSONDecodeError(f"{e.msg} near: {snippet!r}", blob, e.pos) from None

    def generate_until_quality(
        self,
        generate_fn: Callable[[], dict],
        *,
        max_attempts: int = 3,
        retry_wait: int = 0,
    ) -> dict:
        last_score = last_breakdown = last_issues = None
        last_error = None

        for attempt in range(1, max_attempts + 1):
            try:
                script = generate_fn()
                score, breakdown, issues = self.qc.score(script)
                print(
                    f"Quality gate attempt {attempt}/{max_attempts}: "
                    f"{score}/100 {breakdown}"
                )
                if self.qc.passes(score):
                    return script
                last_score, last_breakdown, last_issues = score, breakdown, issues
                print(f"Below threshold ({self.qc.quality_threshold}). Issues: {issues}")
            except Exception as e:
                last_error = e
                print(f"Script attempt {attempt}/{max_attempts} failed: {e}")

            if attempt < max_attempts and retry_wait:
                time.sleep(retry_wait * attempt)

        if last_score is None and last_error is not None:
            raise RuntimeError(
                f"Script generation failed after {max_attempts} attempts: {last_error}"
            ) from last_error

        raise RuntimeError(
            f"Script failed the quality gate after {max_attempts} attempts. "
            f"Last score: {last_score}/100 {last_breakdown}. Issues: {last_issues}"
        )

    def notify_discord(self, content: str, username: str | None = None) -> None:
        self.discord.send(
            DiscordMessage(content=content, username=username or self.discord_username)
        )
