"""Local, self-hosted voice cloning via Chatterbox-Turbo (Resemble AI,
MIT licensed -- fully commercial-safe). Runs entirely on GitHub Actions'
free CPU runner -- no paid API, no GPU needed. Genuinely $0.

The model is loaded once at the class level and shared across every Voice
instance in the process. Each channel still passes its own reference clip.

Real caveats, worth knowing before trusting this in the daily run:
  - No confirmed benchmark exists for this model's CPU speed specifically.
    Expect several minutes per video, not seconds -- test via test.yml
    before relying on this in the daily workflow.
  - Requires Python 3.11 specifically (fails to install on newer versions
    as of early 2026) -- see the workflow files' python-version setting.
  - The published checkpoint was saved with CUDA tensor mappings; loading
    it on a CPU-only machine raises a deserialize error unless patched
    (see patched_torch_load below).
"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import torch

original_torch_load = torch.load


def patched_torch_load(f, map_location=None, **kwargs):
    if map_location is None:
        map_location = "cpu"
    return original_torch_load(f, map_location=map_location, **kwargs)


torch.load = patched_torch_load

import torchaudio as ta  # noqa: E402
from chatterbox.tts_turbo import ChatterboxTurboTTS  # noqa: E402


class Voice:
    """Shared Chatterbox-Turbo model + per-channel reference-clip helpers."""

    model = None

    @classmethod
    def ensure_model_loaded(cls):
        if cls.model is None:
            cls.model = ChatterboxTurboTTS.from_pretrained(device="cpu")
        return cls.model

    def convert_mp3_cached(self, mp3_path: Path, converted_path: Path) -> Path:
        """ffmpeg-convert mp3→wav once; reuse converted_path if it already exists.
        Always mono 24kHz PCM so every male/female ref is the same format for TTS."""
        if converted_path.exists():
            return converted_path
        converted_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(mp3_path),
                "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le",
                str(converted_path),
            ],
            check=True,
        )
        return converted_path

    def resolve_mp3_by_stem(self, mp3_path: Path, cache_dir: Path) -> Path:
        """Convert mp3 → cache_dir/<stem>.wav (cache key = source filename)."""
        return self.convert_mp3_cached(mp3_path, cache_dir / f"{mp3_path.stem}.wav")

    def resolve_reference_clip(
        self, wav_path: Path, mp3_path: Path, converted_path: Path
    ) -> Path:
        """Returns a guaranteed-wav path for a channel's reference clip,
        converting from mp3 via ffmpeg if that's what was provided."""
        if wav_path.exists():
            return wav_path
        if mp3_path.exists():
            return self.convert_mp3_cached(mp3_path, converted_path)
        raise FileNotFoundError(
            f"Missing reference voice clip. Add a 5-20 second recording of the "
            f"target voice at {wav_path} or {mp3_path}."
        )

    def convert_mp3_cached(self, mp3_path: Path, converted_path: Path) -> Path:
        """ffmpeg-convert mp3→wav once; reuse converted_path if it already exists.
        Always mono 24kHz PCM so every male/female ref is the same format for TTS."""
        if converted_path.exists() and self._is_mono_24k(converted_path):
            return converted_path
        converted_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(mp3_path),
                "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le",
                str(converted_path),
            ],
            check=True,
        )
        return converted_path

    @staticmethod
    def _is_mono_24k(path: Path) -> bool:
        try:
            with wave.open(str(path), "rb") as f:
                return f.getnchannels() == 1 and f.getframerate() == 24000
        except Exception:
            return False

    def resolve_mp3_by_stem(self, mp3_path: Path, cache_dir: Path) -> Path:
        """Cache wav as cache_dir/<mp3 stem>.wav — filename is the cache key."""
        return self.convert_mp3_cached(mp3_path, cache_dir / f"{mp3_path.stem}.wav")

    def synthesize_speech(self, text: str, out_path: Path, reference_path: Path) -> None:
        model = self.ensure_model_loaded()
        wav = model.generate(text, audio_prompt_path=str(reference_path))
        # Peak-normalize so OP/OTHER clones land at similar loudness when stitched.
        peak = wav.abs().max().clamp(min=1e-8)
        wav = wav / peak * 0.95
        ta.save(str(out_path), wav, model.sr)

    def wav_duration_seconds(self, path: Path) -> float:
        with wave.open(str(path), "rb") as f:
            return f.getnframes() / f.getframerate()
