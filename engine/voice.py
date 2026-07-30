"""Local, self-hosted voice cloning via Chatterbox-Turbo (Resemble AI,
MIT licensed -- fully commercial-safe). Runs entirely on GitHub Actions'
free CPU runner -- no paid API, no GPU needed. Genuinely $0.

Shared by both pipelines. Both NewNova and RankedbyHetti use the SAME
loaded model instance in-process (loading it is the expensive part,
not conditioning) but each passes its OWN reference clip per call --
that's how Chatterbox voice cloning already works, so two different
channel voices cost nothing extra beyond the one model load.

Real caveats, worth knowing before trusting this in the daily run:
  - No confirmed benchmark exists for this model's CPU speed specifically.
    Expect several minutes per video, not seconds -- test via test.yml
    before relying on this in the daily workflow.
  - Requires Python 3.11 specifically (fails to install on newer versions
    as of early 2026) -- see the workflow files' python-version setting.
  - The published checkpoint was saved with CUDA tensor mappings; loading
    it on a CPU-only machine raises a deserialize error unless patched
    (see _patched_torch_load below)."""

import subprocess
import wave
from pathlib import Path

import torch

_original_torch_load = torch.load


def _patched_torch_load(f, map_location=None, **kwargs):
    if map_location is None:
        map_location = "cpu"
    return _original_torch_load(f, map_location=map_location, **kwargs)


torch.load = _patched_torch_load

import torchaudio as ta  # noqa: E402
from chatterbox.tts_turbo import ChatterboxTurboTTS  # noqa: E402

_model = None


def ensure_model_loaded():
    global _model
    if _model is None:
        _model = ChatterboxTurboTTS.from_pretrained(device="cpu")
    return _model


def resolve_reference_clip(wav_path: Path, mp3_path: Path, converted_path: Path) -> Path:
    """Returns a guaranteed-wav path for a channel's reference clip,
    converting from mp3 via ffmpeg if that's what was provided. Each
    channel passes its own three paths so NewNova's and RankedbyHetti's
    reference clips (and converted-mp3 cache files) never collide."""
    if wav_path.exists():
        return wav_path
    if mp3_path.exists():
        if not converted_path.exists():
            converted_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(mp3_path), str(converted_path)],
                check=True,
            )
        return converted_path
    raise FileNotFoundError(
        f"Missing reference voice clip. Add a 5-20 second recording of the "
        f"target voice at {wav_path} or {mp3_path}."
    )


def synthesize_speech(text: str, out_path: Path, reference_path: Path):
    model = ensure_model_loaded()
    wav = model.generate(text, audio_prompt_path=str(reference_path))
    ta.save(str(out_path), wav, model.sr)


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as f:
        return f.getnframes() / f.getframerate()