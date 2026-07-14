import subprocess
import wave
from pathlib import Path

import requests

VOICES_DIR = Path("voices")
PIPER_MODEL = VOICES_DIR / "en_US-lessac-medium.onnx"
PIPER_MODEL_JSON = VOICES_DIR / "en_US-lessac-medium.onnx.json"
PIPER_VOICE_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "en/en_US/lessac/medium/en_US-lessac-medium.onnx"
)
PIPER_VOICE_JSON_URL = PIPER_VOICE_URL + ".json"


def ensure_piper_voice():
    """Download the voice model if it's not already on disk. Actions
    runners are ephemeral, so this runs fresh every job — fine, it's ~60MB."""
    VOICES_DIR.mkdir(exist_ok=True)
    if not PIPER_MODEL.exists():
        for url, dest in [(PIPER_VOICE_URL, PIPER_MODEL), (PIPER_VOICE_JSON_URL, PIPER_MODEL_JSON)]:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            dest.write_bytes(r.content)


def synthesize_speech(text: str, out_path: Path):
    subprocess.run(
        ["piper", "--model", str(PIPER_MODEL), "--output_file", str(out_path)],
        input=text,
        text=True,
        check=True,
    )


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as f:
        return f.getnframes() / f.getframerate()
