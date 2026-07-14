import subprocess
from pathlib import Path

from pipeline.voice import wav_duration_seconds


def build_segment_clip(image_path: Path, audio_path: Path, out_path: Path):
    """One image + its narration audio -> one clip with a slow zoom (Ken
    Burns effect) so a static photo doesn't feel dead on screen."""
    duration = wav_duration_seconds(audio_path)
    frames = int(duration * 25)  # 25 fps
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(image_path),
            "-i", str(audio_path),
            "-t", str(duration),
            "-vf",
            f"scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080,"
            f"zoompan=z='min(zoom+0.0006,1.2)':d={frames}:s=1920x1080:fps=25",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
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
