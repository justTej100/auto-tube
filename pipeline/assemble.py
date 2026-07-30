import re
import subprocess
from pathlib import Path

from faster_whisper import WhisperModel

from pipeline.voice import wav_duration_seconds

# TikTok / YouTube Shorts vertical frame.
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920

# Fast-caption tuning. 2 words/chunk is the TikTok/Shorts standard --
# enough to read at a glance, fast enough that it never reads like a
# paragraph. Bump to 3 if captions feel too flickery for slower narration.
WORDS_PER_CAPTION = 3

# tiny.en is the fastest faster-whisper model and this is a solved task
# (clean single-speaker TTS audio, no background noise, no accents to
# fight) -- accuracy headroom of base.en/small.en isn't needed here and
# isn't worth the extra CPU time on the free runner. Bump if you ever see
# consistently garbled words in the burned-in captions.
WHISPER_MODEL_SIZE = "tiny.en"

# Distance from the bottom of the frame, in px at the 1080x1920 render
# resolution. Matches the old drawtext y=h*0.62 position (clear of
# TikTok/Shorts UI chrome).
CAPTION_MARGIN_V = int(VIDEO_HEIGHT * (1 - 0.62))

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,DejaVu Sans,84,&H00FFFFFF,&H00000000,&H00000000,-1,0,1,6,0,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Text
"""

_whisper_model: WhisperModel | None = None


def _get_whisper_model() -> WhisperModel:
    """Lazily loads faster-whisper once per process (mirrors the
    ensure_model_loaded() pattern in voice.py). int8 on CPU is the right
    compute_type here -- noticeably faster than float32 with no audible
    accuracy loss on clean TTS audio."""
    global _whisper_model
    if _whisper_model is None:
        _whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    return _whisper_model


def _clean_caption_text(text: str) -> str:
    """Strip dash-style pauses (em/en/--/spaced hyphen) so burned-in
    captions read like spoken Shorts text, not AI prose. Only relevant to
    the estimated-timing fallback path below -- whisper transcribes actual
    speech audio, so its output never contains script-only punctuation."""
    text = re.sub(r"\s*[\u2014\u2013\u2015]\s*", ", ", text)  # — – ―
    text = re.sub(r"\s*--+\s*", ", ", text)
    text = re.sub(r"\s+-\s+", ", ", text)
    text = re.sub(r"\s*,\s*,+", ",", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip(" ,")


def _format_ass_time(seconds: float) -> str:
    """ASS timestamps are H:MM:SS.CS (centiseconds, 2 digits)."""
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    centis = int(round((secs - int(secs)) * 100))
    if centis == 100:  # rounding pushed us into the next second
        centis = 0
        secs = int(secs) + 1
    return f"{hours}:{minutes:02d}:{int(secs):02d}.{centis:02d}"


def _escape_ass_text(text: str) -> str:
    # { and } are ASS override-tag delimiters -- escape any literal braces
    # so they can't be misread as styling commands.
    return text.replace("{", r"\{").replace("}", r"\}")


def _transcribe_words(audio_path: Path) -> list[tuple[str, float, float]]:
    """Runs faster-whisper on the synthesized narration to get real
    per-word timestamps. Returns (word_text, start_seconds, end_seconds)
    tuples in speech order. Raises on failure -- caller decides whether to
    fall back rather than silently mis-syncing captions."""
    model = _get_whisper_model()
    segments, _ = model.transcribe(
        str(audio_path),
        word_timestamps=True,
        # Single-shot narration clips, no reason for whisper to lean on
        # cross-segment context it doesn't have.
        condition_on_previous_text=False,
    )
    words: list[tuple[str, float, float]] = []
    for seg in segments:
        for w in seg.words or []:
            token = w.word.strip()
            if token:
                words.append((token, w.start, w.end))
    if not words:
        raise RuntimeError("faster-whisper returned no words for this clip")
    return words


def _write_caption_ass_aligned(words: list[tuple[str, float, float]], duration: float,
                                out_path: Path, chunk_size: int = WORDS_PER_CAPTION):
    """Real-alignment path: groups whisper's words into chunk_size-word
    captions. Each chunk's end time is pinned to the *next* chunk's start
    (not its own last word's end) so captions hand off with no blank gap
    between them -- matches how CapCut/Opus-style auto-captions behave."""
    lines = [ASS_HEADER.format(width=VIDEO_WIDTH, height=VIDEO_HEIGHT, margin_v=CAPTION_MARGIN_V)]

    for i in range(0, len(words), chunk_size):
        group = words[i : i + chunk_size]
        text = " ".join(w[0] for w in group)
        start = group[0][1]
        end = words[i + chunk_size][1] if i + chunk_size < len(words) else duration
        lines.append(
            f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},"
            f"Caption,,0,0,0,,{_escape_ass_text(text)}\n"
        )

    out_path.write_text("".join(lines))


def _write_caption_ass_estimated(text: str, duration: float, out_path: Path,
                                  chunk_size: int = WORDS_PER_CAPTION):
    """Fallback when whisper transcription fails for any reason (missing
    model download, corrupt audio, etc.) -- estimates timing by splitting
    the known clip duration across word chunks, weighted by character
    count so short words get less screen time than long ones. Less
    accurate than real alignment but keeps the run alive rather than
    failing a whole video over a captioning problem."""
    cleaned = _clean_caption_text(text)
    words = cleaned.split()
    chunks = [" ".join(words[i : i + chunk_size]) for i in range(0, len(words), chunk_size)]
    if not chunks:
        chunks = [cleaned or " "]

    weights = [max(len(c), 1) for c in chunks]
    total_weight = sum(weights)

    lines = [ASS_HEADER.format(width=VIDEO_WIDTH, height=VIDEO_HEIGHT, margin_v=CAPTION_MARGIN_V)]

    t = 0.0
    for chunk, weight in zip(chunks, weights):
        chunk_dur = duration * (weight / total_weight)
        start, end = t, t + chunk_dur
        lines.append(
            f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},"
            f"Caption,,0,0,0,,{_escape_ass_text(chunk)}\n"
        )
        t = end

    out_path.write_text("".join(lines))


def _escape_for_filtergraph(path: Path) -> str:
    """The subtitles filter takes its path as a filter option, where ':'
    is the option separator and '\\' is an escape char -- both need
    escaping even though the value is otherwise safe on Linux."""
    return str(path).replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


def build_segment_clip(video_path: Path, audio_path: Path, out_path: Path,
                        caption_text: str, workdir: Path):
    """One real video clip + narration -> 9:16 Shorts clip with fast,
    whisper-aligned captions. -stream_loop -1 loops short stock clips;
    -t trims to audio length."""
    duration = wav_duration_seconds(audio_path)

    caption_path = workdir / f"{out_path.stem}_caption.ass"
    try:
        words = _transcribe_words(audio_path)
        _write_caption_ass_aligned(words, duration, caption_path)
    except Exception as e:
        print(f"Whisper caption alignment failed ({e}) -- falling back to estimated timing.")
        _write_caption_ass_estimated(caption_text, duration, caption_path)

    caption_filter_path = _escape_for_filtergraph(caption_path)

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
            f"subtitles='{caption_filter_path}'",
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