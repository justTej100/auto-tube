import re
import subprocess
import textwrap
from pathlib import Path

from pipeline.voice import wav_duration_seconds

# TikTok / YouTube Shorts vertical frame.
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920


def _clean_caption_text(text: str) -> str:
    """Strip dash-style pauses (em/en/--/spaced hyphen) so burned-in
    captions read like spoken Shorts text, not AI prose."""
    text = re.sub(r"\s*[\u2014\u2013\u2015]\s*", ", ", text)  # — – ―
    text = re.sub(r"\s*--+\s*", ", ", text)
    text = re.sub(r"\s+-\s+", ", ", text)
    text = re.sub(r"\s*,\s*,+", ",", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip(" ,")


def _write_caption_file(text: str, out_path: Path):
    """Wraps narration for a 1080-wide Shorts frame and writes it for
    ffmpeg drawtext textfile= (avoids fragile inline escaping)."""
    cleaned = _clean_caption_text(text)
    # Narrow wrap so lines fit the vertical frame with large type.
    wrapped = textwrap.fill(cleaned, width=18)
    out_path.write_text(wrapped)


def build_segment_clip(video_path: Path, audio_path: Path, out_path: Path,
                        caption_text: str, workdir: Path):
    """One real video clip + narration -> 9:16 Shorts clip with captions.
    -stream_loop -1 loops short stock clips; -t trims to audio length.
    Captions sit in the lower-middle safe zone (above TikTok/Shorts UI)."""
    duration = wav_duration_seconds(audio_path)

    caption_path = workdir / f"{out_path.stem}_caption.txt"
    _write_caption_file(caption_text, caption_path)

    caption_escaped = str(caption_path).replace("'", r"\'")

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-t", str(duration),
            "-vf",
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
            f"setsar=1,"
            f"fps=30,"
            f"setpts=PTS-STARTPTS,"
            f"drawtext=font='DejaVu Sans Bold':fontsize=54:fontcolor=white:"
            f"borderw=5:bordercolor=black:line_spacing=10:text_align=C:"
            # Centered horizontally; lower-middle (~62% down) stays clear of
            # bottom UI chrome on TikTok / Shorts.
            f"x=(w-text_w)/2:y=(h*0.62):textfile='{caption_escaped}'",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            "-shortest",
            "-avoid_negative_ts", "make_zero",
            str(out_path),
        ],
        check=True,
    )


def concat_clips(clip_paths: list[Path], workdir: Path, out_path: Path):
    list_file = workdir / "concat_list.txt"
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in clip_paths))
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            str(out_path),
        ],
        check=True,
    )


def mix_background_music(video_path: Path, music_path: Path, out_path: Path, music_volume: float = 0.12):
    """Layers background music under the narration at low volume. Music
    loops if shorter than the video, gets trimmed if longer."""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-stream_loop", "-1", "-i", str(music_path),
            "-filter_complex",
            f"[1:a]volume={music_volume}[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            str(out_path),
        ],
        check=True,
    )
