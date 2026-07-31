"""Base class for a channel's daily production run. Both Auto (fully
autonomous, e.g. NewNova) and RankedNiche (human-curated countdown, e.g.
RankedbyHetti) subclass this. Everything here is either:

  - genuinely identical across every channel (Gemini calls, voice
    cloning, whisper-aligned captioning, Drive upload, Discord webhook
    posting) -- concrete methods, never overridden, or
  - a step whose ORDER is fixed but whose CONTENT is channel-specific
    (research vs. intake, freeform script vs. countdown script, stock
    footage vs. sourced clips) -- hooks a subclass must fill in, or
  - a step that's usually the same but occasionally needs tweaking
    (voice setup, cleanup) -- a hook with a sensible default that a
    subclass only overrides if it actually needs to.

run() is the template method: the sequence never changes, only what each
hook does per channel."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from engine import assemble as assemble_mod
from engine import drive as drive_mod
from engine.DiscordNotify import DiscordMessage, DiscordNotifier
from engine.QualityControl import QualityControl
from engine.voice import resolve_reference_clip, synthesize_speech
from engine.voice import wav_duration_seconds as voice_wav_duration_seconds


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
        self.google_client_id = google_client_id
        self.google_client_secret = google_client_secret
        self.google_refresh_token = google_refresh_token
        self.discord_username = discord_username

        self.discord = DiscordNotifier(discord_webhook_url, default_username=discord_username)
        self.qc = QualityControl()

        self.reference_clip: Path | None = None
        self.gemini_client: genai.Client | None = None
        self.drive: Any = None

    # =========================================================
    # Template method -- fixed sequence, hooks do the real work
    # =========================================================

    def run(self) -> str | None:
        self.workdir.mkdir(parents=True, exist_ok=True)

        try:
            context = self.prepare()
            if context is None:
                # Unifies RankedNiche's "no folder ready" with Auto always
                # having work -- Auto's prepare() should just never return None.
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
        """Gathers whatever this channel needs before scripting can start
        (a researched/given topic for Auto; a downloaded intake folder for
        RankedNiche). Return None to skip this run entirely (RankedNiche
        only -- Auto should never return None here)."""
        raise NotImplementedError

    def generate_script(self, context) -> dict:
        raise NotImplementedError

    def render_segments(self, script: dict, context) -> list[Path]:
        """Synthesizes voice + builds each captioned clip. Expected to call
        self.synthesize_speech() / self.transcribe_words() / self.build_segment_clip()
        internally -- those are shared, but the loop shape (which video
        source per segment, whether words get reused for anything else)
        is channel-specific."""
        raise NotImplementedError

    def finalize_assembly(self, clips: list[Path], script: dict, context) -> Path:
        """Concats clips and applies any channel-specific post-processing
        (Auto: background music; RankedNiche: SFX placement)."""
        raise NotImplementedError

    def deliver(self, final_path: Path, script: dict, context) -> str:
        """Uploads to this channel's Drive folder and posts its Discord
        notification. Return the Drive review link."""
        raise NotImplementedError

    # ---- hooks with defaults: override only if this channel needs to ----

    def setup_voice(self) -> None:
        """Resolves this channel's reference clip. Same logic for every
        channel that has ONE fixed reference voice -- a channel with,
        say, multiple rotating voices would override this."""
        self.reference_clip = resolve_reference_clip(
            self.voice_wav, self.voice_mp3, self.voice_converted
        )

    def cleanup(self, context) -> None:
        """No-op by default -- most channels have nothing to clean up
        after a successful run. RankedNiche overrides this to delete the
        processed intake folder."""
        pass

    # =========================================================
    # Shared, concrete: LLM
    # =========================================================

    def get_gemini_client(self) -> genai.Client:
        if self.gemini_client is None:
            self.gemini_client = genai.Client(api_key=self.gemini_api_key)
        return self.gemini_client

    def call_gemini_json(self, prompt: str, attempts: int = 3, retry_wait: int = 10) -> dict:
        """Gemini call in JSON response mode, with retry/backoff on
        transient failures (rate limits, high-demand 503s). Every
        channel's script/placement generation builds its own prompt and
        calls this -- the retry mechanics don't change per channel, only
        the prompt text and what's done with the parsed result."""
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

    def generate_until_quality(
        self,
        generate_fn: Callable[[], dict],
        *,
        max_attempts: int = 3,
        retry_wait: int = 0,
    ) -> dict:
        """Call generate_fn until the script clears QualityControl, or fail."""
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

    # =========================================================
    # Shared, concrete: voice
    # =========================================================

    def synthesize_speech(self, text: str, out_path: Path) -> None:
        if self.reference_clip is None:
            raise RuntimeError("setup_voice() hasn't run yet -- reference clip not resolved")
        synthesize_speech(text, out_path, self.reference_clip)

    def wav_duration_seconds(self, path: Path) -> float:
        return voice_wav_duration_seconds(path)

    # =========================================================
    # Shared, concrete: assembly building blocks
    # =========================================================

    def transcribe_words(self, audio_path: Path) -> list[tuple[str, float, float]]:
        return assemble_mod.transcribe_words(audio_path)

    def build_segment_clip(
        self,
        video_path: Path,
        audio_path: Path,
        out_path: Path,
        caption_text: str,
        precomputed_words=None,
    ) -> None:
        assemble_mod.build_segment_clip(
            video_path,
            audio_path,
            out_path,
            caption_text,
            self.workdir,
            precomputed_words=precomputed_words,
        )

    def concat_clips(self, clip_paths: list[Path], out_path: Path) -> None:
        assemble_mod.concat_clips(clip_paths, self.workdir, out_path)

    def mix_background_music(
        self,
        video_path: Path,
        music_path: Path,
        out_path: Path,
        music_volume: float = 0.12,
    ) -> None:
        assemble_mod.mix_background_music(video_path, music_path, out_path, music_volume)

    def mix_sfx_events(
        self,
        video_path: Path,
        events: list[tuple[Path, float]],
        out_path: Path,
        sfx_volume: float = 0.9,
    ) -> None:
        assemble_mod.mix_sfx_events(video_path, events, out_path, sfx_volume)

    # =========================================================
    # Shared, concrete: delivery
    # =========================================================

    def get_drive(self):
        if self.drive is None:
            self.drive = drive_mod.build_client(
                self.google_client_id,
                self.google_client_secret,
                self.google_refresh_token,
            )
        return self.drive

    def upload_to_drive(self, folder_id: str, file_path: Path, filename: str) -> str:
        return drive_mod.upload_file(self.get_drive(), folder_id, file_path, filename)

    def notify_discord(self, content: str, username: str | None = None) -> None:
        self.discord.send(
            DiscordMessage(content=content, username=username or self.discord_username)
        )
