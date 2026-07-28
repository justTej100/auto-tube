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


def build_segment_clip(video_path: Path, audio_path: Path, out_path: Path,
                        caption_text: str, workdir: Path):
    """One real video clip + its narration audio -> one clip with burned-in
    captions. -stream_loop -1 loops the source clip if it's shorter than
    the narration, and -t trims to the exact needed length either way --
    handles both "clip longer than audio" and "clip shorter than audio"
    with the same command.

    Real footage already has motion, so unlike the old still-image version
    there's no zoompan filter here. Source audio is discarded; only the
    narration track is kept. setpts resets timestamps after looping so
    concat doesn't inherit non-monotonic DTS from looped stock clips."""
    duration = wav_duration_seconds(audio_path)

    caption_path = workdir / f"{out_path.stem}_caption.txt"
    _write_caption_file(caption_text, caption_path)

    # Escape single quotes for ffmpeg's textfile= filter option.
    caption_escaped = str(caption_path).replace("'", r"\'")

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-t", str(duration),
            "-vf",
            f"scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080,"
            f"setsar=1,"
            f"fps=30,"
            f"setpts=PTS-STARTPTS,"
            f"drawtext=font='DejaVu Sans Bold':fontsize=64:fontcolor=white:"
            f"borderw=4:bordercolor=black:line_spacing=12:text_align=C:"
            f"x=(w-text_w)/2:y=h-300:textfile='{caption_escaped}'",
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
    # Re-encode on concat instead of -c copy: segment clips are already
    # normalized, but copy-mode is brittle if any clip still has slightly
    # different timing metadata after looping.
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
