"""Kokoro TTS voiceover generation. Apache 2.0 license (fully commercial-safe,
same as Piper), no API key, runs locally. Swapped in over Piper for better
narration quality -- our pipeline renders in a batch job with no latency
pressure, so Piper's speed advantage doesn't matter here and Kokoro's more
natural voice does.

Model weights (~327MB) download automatically on first use via
huggingface_hub, cached under whatever HF_HOME points to -- see
publish.yml, which sets HF_HOME to a workspace path and caches it with
actions/cache so this only downloads once, not every run."""

import wave
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro import KPipeline

_pipeline = None


def ensure_kokoro_pipeline():
    """Loads the pipeline (and triggers the one-time model download if the
    HF cache is empty). Called once at pipeline start so the download
    happens up front rather than surprising the first synthesize_speech call."""
    global _pipeline
    if _pipeline is None:
        _pipeline = KPipeline(lang_code="a")  # 'a' = American English
    return _pipeline


def synthesize_speech(text: str, out_path: Path, voice: str = "af_heart"):
    pipeline = ensure_kokoro_pipeline()
    chunks = [audio for _, _, audio in pipeline(text, voice=voice)]
    full_audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    sf.write(str(out_path), full_audio, 24000)


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as f:
        return f.getnframes() / f.getframerate()
