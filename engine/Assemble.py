"""ffmpeg/whisper editing: captioned segments, concat, BGM, SFX."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from faster_whisper import WhisperModel

from engine.Voice import Voice

# TikTok / YouTube Shorts vertical frame.
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920

# Fast-caption tuning. 2 words/chunk is the TikTok/Shorts standard.
WORDS_PER_CAPTION = 2

WHISPER_MODEL_SIZE = "tiny.en"

# Distance from the bottom of the frame, in px at the 1080x1920 render
# resolution. Clear of TikTok/Shorts UI chrome.
CAPTION_MARGIN_V = int(VIDEO_HEIGHT * (1 - 0.62))

# Caption script template (SubStation format fired through ffmpeg's subtitles filter).
CAPTION_HEADER = """[Script Info]
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


class Assemble:
    """Shared editing helpers for every channel. Owns the workdir for
    temp caption/concat files; uses Voice for wav duration."""

    whisper_model: WhisperModel | None = None

    def __init__(self, workdir: Path, voice: Voice) -> None:
        self.workdir = workdir
        self.voice = voice

    @classmethod
    def ensure_whisper_loaded(cls) -> WhisperModel:
        if cls.whisper_model is None:
            cls.whisper_model = WhisperModel(
                WHISPER_MODEL_SIZE, device="cpu", compute_type="int8"
            )
        return cls.whisper_model

    def clean_caption_text(self, text: str) -> str:
        text = re.sub(r"\s*[\u2014\u2013\u2015]\s*", ", ", text)
        text = re.sub(r"\s*--+\s*", ", ", text)
        text = re.sub(r"\s+-\s+", ", ", text)
        text = re.sub(r"\s*,\s*,+", ",", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip(" ,")

    def format_caption_time(self, seconds: float) -> str:
        """Caption timestamps are H:MM:SS.CS (centiseconds, 2 digits)."""
        seconds = max(0.0, seconds)
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        centis = int(round((secs - int(secs)) * 100))
        if centis == 100:
            centis = 0
            secs = int(secs) + 1
        return f"{hours}:{minutes:02d}:{int(secs):02d}.{centis:02d}"

    def escape_caption_text(self, text: str) -> str:
        # { and } are override-tag delimiters -- escape any literal braces
        # so they can't be misread as styling commands.
        return text.replace("{", r"\{").replace("}", r"\}")

    def escape_for_filtergraph(self, path: Path) -> str:
        return str(path).replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")

    def transcribe_words(self, audio_path: Path) -> list[tuple[str, float, float]]:
        model = self.ensure_whisper_loaded()
        segments, _ = model.transcribe(
            str(audio_path),
            word_timestamps=True,
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

    def write_caption_aligned(
        self,
        words: list[tuple[str, float, float]],
        duration: float,
        out_path: Path,
        chunk_size: int = WORDS_PER_CAPTION,
    ) -> None:
        lines = [
            CAPTION_HEADER.format(
                width=VIDEO_WIDTH, height=VIDEO_HEIGHT, margin_v=CAPTION_MARGIN_V
            )
        ]

        for i in range(0, len(words), chunk_size):
            group = words[i : i + chunk_size]
            text = " ".join(w[0] for w in group)
            start = group[0][1]
            end = words[i + chunk_size][1] if i + chunk_size < len(words) else duration
            # Must match Events Format: Layer, Start, End, Style, Text
            lines.append(
                f"Dialogue: 0,{self.format_caption_time(start)},{self.format_caption_time(end)},"
                f"Caption,{self.escape_caption_text(text)}\n"
            )

        out_path.write_text("".join(lines))

    def write_caption_estimated(
        self,
        text: str,
        duration: float,
        out_path: Path,
        chunk_size: int = WORDS_PER_CAPTION,
    ) -> None:
        cleaned = self.clean_caption_text(text)
        words = cleaned.split()
        chunks = [
            " ".join(words[i : i + chunk_size]) for i in range(0, len(words), chunk_size)
        ]
        if not chunks:
            chunks = [cleaned or " "]

        weights = [max(len(c), 1) for c in chunks]
        total_weight = sum(weights)

        lines = [
            CAPTION_HEADER.format(
                width=VIDEO_WIDTH, height=VIDEO_HEIGHT, margin_v=CAPTION_MARGIN_V
            )
        ]

        t = 0.0
        for chunk, weight in zip(chunks, weights):
            chunk_dur = duration * (weight / total_weight)
            start, end = t, t + chunk_dur
            # Must match Events Format: Layer, Start, End, Style, Text
            lines.append(
                f"Dialogue: 0,{self.format_caption_time(start)},{self.format_caption_time(end)},"
                f"Caption,{self.escape_caption_text(chunk)}\n"
            )
            t = end

        out_path.write_text("".join(lines))

    def build_segment_clip(
        self,
        video_path: Path,
        audio_path: Path,
        out_path: Path,
        caption_text: str,
        precomputed_words: list[tuple[str, float, float]] | None = None,
    ) -> None:
        # Audio is the duration authority — never cut narration short.
        duration = self.voice.wav_duration_seconds(audio_path)

        # Temp caption script for ffmpeg's subtitles filter. Extension must
        # stay a format ffmpeg recognizes (.ass); our code calls these captions.
        caption_path = self.workdir / f"{out_path.stem}_caption.ass"
        try:
            words = (
                precomputed_words
                if precomputed_words is not None
                else self.transcribe_words(audio_path)
            )
            self.write_caption_aligned(words, duration, caption_path)
        except Exception as e:
            print(
                f"Whisper caption alignment failed ({e}) -- falling back to estimated timing."
            )
            self.write_caption_estimated(caption_text, duration, caption_path)

        caption_filter_path = self.escape_for_filtergraph(caption_path)

        # Loop stock video; end exactly when narration ends (-shortest on
        # infinite video + finite audio). apad keeps the last syllable from
        # getting eaten by the AAC encoder. No -t — that fought full audio.
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-stream_loop", "-1",
                "-i", str(video_path),
                "-i", str(audio_path),
                "-filter_complex",
                f"[0:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
                f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
                f"setsar=1,fps=30,setpts=PTS-STARTPTS,"
                f"subtitles='{caption_filter_path}'[v];"
                f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,"
                f"apad=pad_dur=0.08[a]",
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "veryfast",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "44100", "-ac", "2",
                "-shortest",
                "-avoid_negative_ts", "make_zero",
                str(out_path),
            ],
            check=True,
        )

    def concat_clips(self, clip_paths: list[Path], out_path: Path) -> None:
        """Stitch clips back-to-back; each clip's full audio plays before the next."""
        if len(clip_paths) == 1:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(clip_paths[0]), "-c", "copy", str(out_path)],
                check=True,
            )
            return

        inputs: list[str] = []
        for p in clip_paths:
            inputs += ["-i", str(p)]
        n = len(clip_paths)
        # Reset timestamps per stream so concat doesn't drop early audio.
        normalized = "".join(
            f"[{i}:v]setpts=PTS-STARTPTS[v{i}];"
            f"[{i}:a]asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=44100:channel_layouts=stereo[a{i}];"
            for i in range(n)
        )
        stream_pairs = "".join(f"[v{i}][a{i}]" for i in range(n))
        filter_complex = (
            f"{normalized}{stream_pairs}concat=n={n}:v=1:a=1[v][a]"
        )
        subprocess.run(
            [
                "ffmpeg", "-y",
                *inputs,
                "-filter_complex", filter_complex,
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "44100", "-ac", "2",
                str(out_path),
            ],
            check=True,
        )

    def mix_background_music(
        self,
        video_path: Path,
        music_path: Path,
        out_path: Path,
        music_volume: float = 0.12,
    ) -> None:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-stream_loop", "-1", "-i", str(music_path),
                "-filter_complex",
                f"[1:a]volume={music_volume}[music];"
                f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[a]",
                "-map", "0:v", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", "-shortest",
                str(out_path),
            ],
            check=True,
        )

    def mix_sfx_events(
        self,
        video_path: Path,
        events: list[tuple[Path, float]],
        out_path: Path,
        sfx_volume: float = 0.9,
    ) -> None:
        if not events:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(video_path), "-c", "copy", str(out_path)],
                check=True,
            )
            return

        inputs = ["-i", str(video_path)]
        for sfx_path, _ in events:
            inputs += ["-i", str(sfx_path)]

        filter_parts = []
        mix_labels = ["0:a"]
        for i, (_, timestamp) in enumerate(events, start=1):
            delay_ms = max(0, round(timestamp * 1000))
            filter_parts.append(
                f"[{i}:a]adelay={delay_ms}:all=1,volume={sfx_volume}[sfx{i}]"
            )
            mix_labels.append(f"sfx{i}")

        mix_inputs = "".join(f"[{label}]" for label in mix_labels)
        filter_parts.append(
            f"{mix_inputs}amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=0[a]"
        )
        filter_complex = ";".join(filter_parts)

        subprocess.run(
            [
                "ffmpeg", "-y",
                *inputs,
                "-filter_complex", filter_complex,
                "-map", "0:v", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac",
                "-shortest",
                str(out_path),
            ],
            check=True,
        )
