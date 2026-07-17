import subprocess
import textwrap
from pathlib import Path

from pipeline.voice import wav_duration_seconds


def _write_caption_file(text: str, out_path: Path):
    """Wraps narration text to a readable line width and writes it to a
    file -- ffmpeg's drawtext textfile= avoids the notoriously fragile
    inline text escaping (colons, quotes) that trips people up constantly."""
    wrapped = textwrap.fill(text, width=32)
    out_path.write_text(wrapped)


def build_segment_clip(image_path: Path, audio_path: Path, out_path: Path,
                        caption_text: str, workdir: Path):
    """One image + its narration audio -> one clip with a slow zoom (Ken
    Burns effect) and burned-in captions.

    zoompan is one of ffmpeg's slowest filters (it recomputes every frame
    individually), so this renders at 20fps instead of 25 -- imperceptible
    for a slow pan/zoom, but a real cut in frame count. preset=veryfast and
    tune=stillimage (built for exactly this kind of mostly-static content)
    also meaningfully speed up encoding."""
    duration = wav_duration_seconds(audio_path)
    fps = 20
    frames = int(duration * fps)

    caption_path = workdir / f"{out_path.stem}_caption.txt"
    _write_caption_file(caption_text, caption_path)

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(image_path),
            "-i", str(audio_path),
            "-t", str(duration),
            "-vf",
            f"scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080,"
            f"zoompan=z='min(zoom+0.0006,1.2)':d={frames}:s=1920x1080:fps={fps},"
            f"drawtext=font='DejaVu Sans Bold':fontsize=64:fontcolor=white:"
            f"borderw=4:bordercolor=black:line_spacing=12:text_align=C:"
            f"x=(w-text_w)/2:y=h-300:textfile='{caption_path}'",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(out_path),
        ],
        check=True,
    )


def concat_clips(clip_paths: list[Path], workdir: Path, out_path: Path):
    list_file = workdir / "concat_list.txt"
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in clip_paths))
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)],
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
