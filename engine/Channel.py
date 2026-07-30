"""Base class for a channel's daily production run. Both Auto (fully
autonomous, e.g. NewNova) and RankedNiche (human-curated countdown, e.g.
RankedbyHetti) subclass this. Everything here is either:

  - genuinely identical across every channel (Gemini calls, voice
    cloning, whisper-aligned captioning, Drive upload, Discord webhook
    posting) -- concrete methods, never overridden, or
  - a step whose ORDER is fixed but whose CONTENT is channel-specific
    (research vs. intake, freeform script vs. countdown script, stock
    footage vs. sourced clips) -- abstract hooks a subclass must fill in,
    or
  - a step that's usually the same but occasionally needs tweaking
    (voice setup, cleanup) -- a hook with a sensible default that a
    subclass only overrides if it actually needs to.

run() is the template method: the sequence never changes, only what each
hook does per channel."""

import json
import time
from pathlib import Path

from google import genai
from google.genai import types

from studio.assemble import build_segment_clip as _build_segment_clip
from studio.assemble import concat_clips as _concat_clips
from studio.assemble import transcribe_words as _transcribe_words
from studio.discord_notify import send_message
from studio.drive import build_client as _build_drive_client
from studio.drive import upload_file as _upload_file
from studio.voice import resolve_reference_clip, synthesize_speech
from studio.voice import wav_duration_seconds as _wav_duration_seconds


class Channel:
    def __init__(self, gemini_api_key: str, hf_token: str | None, workdir: Path,
                 voice_wav: Path, voice_mp3: Path, voice_converted: Path,
                 google_client_id: str, google_client_secret: str, google_refresh_token: str):
        self.gemini_api_key = gemini_api_key
        self.hf_token = hf_token
        self.workdir = workdir
        self._voice_wav = voice_wav
        self._voice_mp3 = voice_mp3
        self._voice_converted = voice_converted
        self.google_client_id = google_client_id
        self.google_client_secret = google_client_secret
        self.google_refresh_token = google_refresh_token

        self._reference_clip: Path | None = None   # set by _setup_voice()
        self._gemini_client = None                  # lazy, cached across calls
        self._drive_client = None                   # lazy, cached across calls

    # =========================================================
    # Template method -- fixed sequence, hooks do the real work
    # =========================================================

    def run(self) -> str | None:
        self.workdir.mkdir(parents=True, exist_ok=True)

        context = self._prepare()
        if context is None:
            # Unifies RankedNiche's "no folder ready" with Auto always
            # having work -- Auto's _prepare() should just never return None.
            return None

        self._setup_voice()
        script = self._generate_script(context)
        clips = self._render_segments(script, context)
        final_path = self._finalize_assembly(clips, script, context)
        result = self._deliver(final_path, script, context)
        self._cleanup(context)

        return result

    # ---- abstract hooks: every subclass must implement these ----

    def _prepare(self):
        """Gathers whatever this channel needs before scripting can start
        (a researched/given topic for Auto; a downloaded intake folder for
        RankedNiche). Return None to skip this run entirely (RankedNiche
        only -- Auto should never return None here)."""
        raise NotImplementedError

    def _generate_script(self, context) -> dict:
        raise NotImplementedError

    def _render_segments(self, script: dict, context) -> list[Path]:
        """Synthesizes voice + builds each captioned clip. Expected to call
        self.synthesize_speech() / self.transcribe_words() / self.build_segment_clip()
        internally -- those are shared, but the loop shape (which video
        source per segment, whether words get reused for anything else)
        is channel-specific."""
        raise NotImplementedError

    def _finalize_assembly(self, clips: list[Path], script: dict, context) -> Path:
        """Concats clips and applies any channel-specific post-processing
        (Auto: background music; RankedNiche: SFX placement)."""
        raise NotImplementedError

    def _deliver(self, final_path: Path, script: dict, context) -> str:
        """Uploads to this channel's Drive folder and posts its Discord
        notification. Return the Drive review link."""
        raise NotImplementedError

    # ---- hooks with defaults: override only if this channel needs to ----

    def _setup_voice(self) -> None:
        """Resolves this channel's reference clip. Same logic for every
        channel that has ONE fixed reference voice -- a channel with,
        say, multiple rotating voices would override this."""
        self._reference_clip = resolve_reference_clip(
            self._voice_wav, self._voice_mp3, self._voice_converted
        )

    def _cleanup(self, context) -> None:
        """No-op by default -- most channels have nothing to clean up
        after a successful run. RankedNiche overrides this to delete the
        processed intake folder."""
        pass

    # =========================================================
    # Shared, concrete: LLM
    # =========================================================

    def _client(self) -> genai.Client:
        if self._gemini_client is None:
            self._gemini_client = genai.Client(api_key=self.gemini_api_key)
        return self._gemini_client

    def call_gemini_json(self, prompt: str, attempts: int = 3, retry_wait: int = 10) -> dict:
        """Gemini call in JSON response mode, with retry/backoff on
        transient failures (rate limits, high-demand 503s). Every
        channel's script/placement generation builds its own prompt and
        calls this -- the retry mechanics don't change per channel, only
        the prompt text and what's done with the parsed result."""
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                resp = self._client().models.generate_content(
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

    # =========================================================
    # Shared, concrete: voice
    # =========================================================

    def synthesize_speech(self, text: str, out_path: Path) -> None:
        if self._reference_clip is None:
            raise RuntimeError("_setup_voice() hasn't run yet -- reference clip not resolved")
        synthesize_speech(text, out_path, self._reference_clip)

    def wav_duration_seconds(self, path: Path) -> float:
        return _wav_duration_seconds(path)

    # =========================================================
    # Shared, concrete: assembly building blocks
    # =========================================================

    def transcribe_words(self, audio_path: Path) -> list[tuple[str, float, float]]:
        return _transcribe_words(audio_path)

    def build_segment_clip(self, video_path: Path, audio_path: Path, out_path: Path,
                            caption_text: str, precomputed_words=None) -> None:
        _build_segment_clip(
            video_path, audio_path, out_path, caption_text, self.workdir,
            precomputed_words=precomputed_words,
        )

    def concat_clips(self, clip_paths: list[Path], out_path: Path) -> None:
        _concat_clips(clip_paths, self.workdir, out_path)

    # =========================================================
    # Shared, concrete: delivery
    # =========================================================

    def _drive(self):
        if self._drive_client is None:
            self._drive_client = _build_drive_client(
                self.google_client_id, self.google_client_secret, self.google_refresh_token
            )
        return self._drive_client

    def upload_to_drive(self, folder_id: str, file_path: Path, filename: str) -> str:
        return _upload_file(self._drive(), folder_id, file_path, filename)

    def notify_discord(self, webhook_url: str, content: str, username: str | None = None) -> None:
        send_message(webhook_url, content, username=username)