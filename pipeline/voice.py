"""Local, self-hosted voice cloning via Chatterbox-Turbo (Resemble AI,
MIT licensed -- fully commercial-safe). Runs entirely on GitHub Actions'
free CPU runner -- no paid API, no GPU needed. Genuinely $0.

Real caveats, worth knowing before trusting this in the daily run:
  - No confirmed benchmark exists for this model's CPU speed specifically.
    Expect several minutes per video, not seconds -- test via test.yml
    before relying on this in the parallel matrix workflow.
  - Requires Python 3.11 specifically (fails to install on newer versions
    as of early 2026) -- see the workflow files' python-version setting.
  - The published checkpoint was saved with CUDA tensor mappings; loading
    it on a CPU-only machine raises a deserialize error unless patched
    (see _patched_torch_load below).

Put your reference clip (5-20 seconds of clean audio, your own voice, one
speaker, minimal background noise) at assets/voice_reference.wav OR
assets/voice_reference.mp3 -- mp3 gets auto-converted to wav via ffmpeg
before use, since Chatterbox's audio loading behavior with mp3 directly
isn't something worth gambling on when ffmpeg conversion is one line."""

import subprocess
import wave
from pathlib import Path

import torch

REFERENCE_WAV_PATH = Path("assets/voice_reference.wav")
REFERENCE_MP3_PATH = Path("assets/voice_reference.mp3")
CONVERTED_REFERENCE_PATH = Path("build/voice_reference_converted.wav")

_original_torch_load = torch.load


def _patched_torch_load(f, map_location=None, **kwargs):
    if map_location is None:
        map_location = "cpu"
    return _original_torch_load(f, map_location=map_location, **kwargs)


torch.load = _patched_torch_load

import torchaudio as ta  # noqa: E402
from chatterbox.tts_turbo import ChatterboxTurboTTS  # noqa: E402

_model = None


def _resolve_reference_path() -> Path:
    """Returns a guaranteed-wav path for the reference clip, converting
    from mp3 via ffmpeg if that's what was provided."""
    if REFERENCE_WAV_PATH.exists():
        return REFERENCE_WAV_PATH
    if REFERENCE_MP3_PATH.exists():
        if not CONVERTED_REFERENCE_PATH.exists():
            CONVERTED_REFERENCE_PATH.parent.mkdir(exist_ok=True)
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(REFERENCE_MP3_PATH), str(CONVERTED_REFERENCE_PATH)],
                check=True,
            )
        return CONVERTED_REFERENCE_PATH
    raise FileNotFoundError(
        "Missing reference voice clip. Add a 5-20 second recording of the "
        "target voice at assets/voice_reference.wav or assets/voice_reference.mp3."
    )


def ensure_model_loaded():
    global _model
    if _model is None:
        _resolve_reference_path()  # fail fast if it's missing, before loading the model
        _model = ChatterboxTurboTTS.from_pretrained(device="cpu")
    return _model


def synthesize_speech(text: str, out_path: Path):
    model = ensure_model_loaded()
    reference_path = _resolve_reference_path()
    wav = model.generate(text, audio_prompt_path=str(reference_path))
    ta.save(str(out_path), wav, model.sr)


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as f:
        return f.getnframes() / f.getframerate()