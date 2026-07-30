"""RankedNiche: human-curated ranked-countdown channel production. A
person sources 5 real clips per topic and drops them (with a manifest
describing each one) into a Drive folder-of-folders. This channel writes
countdown narration grounded in that manifest, places sound effects, and
assembles -- no stock footage search, no autonomous topic research. This
is the pattern RankedbyHetti runs on.

Runs opportunistically: _prepare() returns None (via run()'s short
circuit) when no intake folder is ready yet, which is the normal state
most of the time."""

import json
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from studio.channel import Channel
from studio import drive
from studio.discord_notify import send_message
from studio.quality import QUALITY_THRESHOLD, score_script

MAX_SCRIPT_RETRIES = 3
REQUIRED_CLIP_NUMBERS = {"1", "2", "3", "4", "5"}
MANIFEST_FILENAME = "manifest.json"
CATALOG_FILENAME = "catalog.json"
MIN_SFX_MATCH_OVERLAP = 0.34  # need at least ~1/3 of trigger-phrase tokens to line up

SCRIPT_PROMPT_TEMPLATE = """Write narration for a "Top 5 {topic}" countdown video.

Five real clips are already chosen, ranked #1 (the best/most extreme) down
to #5 (the weakest of the five). Here's what's actually in each one,
described by whoever sourced them -- ground every line in these specifics,
never write something generic that could apply to any top-5 list:

{manifest_block}

Write ONE narration segment per rank, in countdown order: start at #5,
end at #1, so the biggest one lands last as the payoff.

- The #5 segment MUST open with a hook, a question, or a claim that earns
  the next 3 seconds -- never a boring "let's start with..." line.
- Ground every segment in the specific facts from that item's description
  above -- names, numbers, places. Never invent details not in the
  description.
- Vary sentence rhythm, write like a person talking to a friend.
- Never use em dashes, en dashes, or hyphen-as-pause (no "—", "–", or
  "word - word"). Captions are burned on screen.
- Avoid AI-writing tells: no "leverage", "delve", "landscape", "robust",
  "testament", "pivotal", "seamless", or similar. No "it's not just X,
  it's Y".
- The #1 segment should land as a genuine payoff, not a flat restatement.

Return JSON in this exact shape:
{{
  "title": "...",
  "description": "...",
  "segments": [
    {{"clip_number": 5, "narration": "..."}},
    {{"clip_number": 4, "narration": "..."}},
    {{"clip_number": 3, "narration": "..."}},
    {{"clip_number": 2, "narration": "..."}},
    {{"clip_number": 1, "narration": "..."}}
  ]
}}

JSON rules: double quotes only, no trailing commas, no comments, escape
inner double quotes as \\". """


@dataclass(frozen=True)
class _SfxCatalogEntry:
    id: str
    file: str
    description: str


@dataclass(frozen=True)
class _SfxEvent:
    segment_index: int
    sfx_id: str
    sfx_path: Path
    timestamp_seconds: float


@dataclass(frozen=True)
class _RankedNicheContext:
    """What _prepare() gathers: the ready folder's contents, or None if
    nothing's ready this run."""
    folder_id: str
    topic: str
    clips: dict[str, Path]      # {'1': local_path, ..., '5': local_path}
    manifest: dict


class RankedNiche(Channel):
    def __init__(self, gemini_api_key: str, hf_token: str | None,
                 discord_webhook_url: str,
                 google_client_id: str, google_client_secret: str, google_refresh_token: str,
                 drive_intake_folder_id: str, drive_output_folder_id: str,
                 workdir: Path = Path("build/rankedniche"),
                 voice_wav: Path = Path("assets/rankedniche/voice_reference.wav"),
                 voice_mp3: Path = Path("assets/rankedniche/voice_reference.mp3"),
                 voice_converted: Path = Path("build/rankedniche/voice_reference_converted.wav"),
                 sfx_dir: Path = Path("assets/rankedniche/sfx")):
        super().__init__(
            gemini_api_key, hf_token, workdir, voice_wav, voice_mp3, voice_converted,
            google_client_id, google_client_secret, google_refresh_token,
        )
        self.discord_webhook_url = discord_webhook_url
        self.drive_intake_folder_id = drive_intake_folder_id
        self.drive_output_folder_id = drive_output_folder_id
        self.sfx_dir = sfx_dir

        # populated during _render_segments(), consumed during
        # _finalize_assembly() -- SFX placement needs both, and needs
        # each segment's real whisper timing without re-transcribing.
        self._segment_words: list[list[tuple[str, float, float]]] = []
        self._segment_offsets: list[float] = []
        self._sfx_event_count = 0

    # =========================================================
    # Template hooks
    # =========================================================

    def _prepare(self) -> _RankedNicheContext | None:
        ready = self._find_ready_folder()
        if ready is None:
            return None

        print(f"RankedNiche folder ready: {ready['folder_name']}")
        downloaded = self._download_folder_contents(ready, self.workdir / "source")
        return _RankedNicheContext(
            folder_id=ready["folder_id"],
            topic=ready["folder_name"],
            clips=downloaded["clips"],
            manifest=downloaded["manifest"],
        )

    def _generate_script(self, context: _RankedNicheContext) -> dict:
        script = self._generate_countdown_script(context.topic, context.manifest)
        print(f"RankedNiche script generated: {script['title']}")
        return script

    def _render_segments(self, script: dict, context: _RankedNicheContext) -> list[Path]:
        self._segment_words = []
        self._segment_offsets = []
        cumulative = 0.0

        clip_paths = []
        for i, seg in enumerate(script["segments"]):
            clip_number = str(seg["clip_number"])
            print(f"Segment {i} (clip #{clip_number}): {seg['narration'][:60]}...")

            audio_path = self.workdir / f"seg_{i}.wav"
            clip_path = self.workdir / f"seg_{i}.mp4"
            source_video = context.clips[clip_number]

            self.synthesize_speech(seg["narration"], audio_path)
            # Transcribed once, reused for captions AND sfx timing below --
            # avoids running whisper twice per clip.
            words = self.transcribe_words(audio_path)
            self._segment_words.append(words)
            self._segment_offsets.append(cumulative)
            cumulative += self.wav_duration_seconds(audio_path)

            self.build_segment_clip(source_video, audio_path, clip_path, seg["narration"],
                                     precomputed_words=words)
            clip_paths.append(clip_path)

        return clip_paths

    def _finalize_assembly(self, clips: list[Path], script: dict, context: _RankedNicheContext) -> Path:
        concat_path = self.workdir / "concat.mp4"
        self.concat_clips(clips, concat_path)

        catalog = self._load_sfx_catalog()
        sfx_events = []
        if catalog:
            placements = self._pick_sfx_placements(script, catalog)
            resolved = self._resolve_sfx_timestamps(placements, catalog)
            sfx_events = [(e.sfx_path, e.timestamp_seconds) for e in resolved]
            print(f"Placed {len(sfx_events)} sound effect(s).")
        self._sfx_event_count = len(sfx_events)

        final_path = self.workdir / "final.mp4"
        self.mix_sfx(concat_path, sfx_events, final_path)
        print(f"RankedNiche video assembled: {final_path}")
        return final_path

    def _deliver(self, final_path: Path, script: dict, context: _RankedNicheContext) -> str:
        filename = f"{date.today().isoformat()} - {script['title']}.mp4"
        drive_link = self.upload_to_drive(self.drive_output_folder_id, final_path, filename)
        print(f"Uploaded to Drive: {drive_link}")

        sfx_note = f"\nSFX placed: {self._sfx_event_count}" if self._sfx_event_count else ""
        self.notify_discord(
            self.discord_webhook_url,
            f"🎬 New RankedNiche video ready for review: **{script['title']}**\n"
            f"{drive_link}\n"
            f"Source folder: {context.topic}{sfx_note}",
            username="rankedniche",
        )
        print("Discord notification sent.")
        return drive_link

    def _cleanup(self, context: _RankedNicheContext) -> None:
        """Permanently deletes the intake folder once its video has been
        fully assembled and uploaded, so processed folders don't pile up."""
        drive_client = self._drive()
        drive.delete_folder(drive_client, context.folder_id)
        print(f"Deleted intake folder: {context.topic}")

    # =========================================================
    # RankedNiche-only: Drive intake scanning
    # =========================================================

    def _stem(self, filename: str) -> str:
        return filename.rsplit(".", 1)[0]

    def _find_ready_folder(self) -> dict | None:
        """A subfolder is ready once it has 5 clips named 1-5 (any
        extension) plus manifest.json describing each one -- the script
        writer has no other way to know what a clip shows, since it never
        watches the footage. Partial folders are logged and left alone,
        not treated as an error -- that's the normal state while clips
        are still being uploaded."""
        drive_client = self._drive()
        for folder in drive.list_subfolders(drive_client, self.drive_intake_folder_id):
            files = drive.list_files(drive_client, folder["id"])
            clip_files = {}
            manifest_file = None
            for f in files:
                stem = self._stem(f["name"])
                if stem in REQUIRED_CLIP_NUMBERS:
                    clip_files[stem] = f
                elif f["name"].lower() == MANIFEST_FILENAME:
                    manifest_file = f

            if len(clip_files) == 5 and manifest_file:
                return {"folder_id": folder["id"], "folder_name": folder["name"],
                        "clip_files": clip_files, "manifest_file": manifest_file}
            elif clip_files or manifest_file:
                missing = REQUIRED_CLIP_NUMBERS - clip_files.keys()
                note = f"missing clips {sorted(missing)}" if missing else f"missing {MANIFEST_FILENAME}"
                print(f"RankedNiche folder '{folder['name']}' not ready yet ({note}) -- skipping for now.")

        return None

    def _download_folder_contents(self, ready_folder: dict, workdir: Path) -> dict:
        drive_client = self._drive()
        workdir.mkdir(parents=True, exist_ok=True)

        clips = {}
        for number, file_info in ready_folder["clip_files"].items():
            ext = Path(file_info["name"]).suffix or ".mp4"
            out_path = workdir / f"clip_{number}{ext}"
            drive.download_file(drive_client, file_info["id"], out_path)
            clips[number] = out_path

        manifest_path = workdir / MANIFEST_FILENAME
        drive.download_file(drive_client, ready_folder["manifest_file"]["id"], manifest_path)
        manifest = json.loads(manifest_path.read_text())

        return {"clips": clips, "manifest": manifest}

    # =========================================================
    # RankedNiche-only: countdown script generation
    # =========================================================

    def _manifest_block(self, manifest: dict) -> str:
        lines = []
        for n in ["1", "2", "3", "4", "5"]:
            desc = manifest.get(n, "(no description provided -- narration for this one will be generic)")
            lines.append(f"#{n}: {desc}")
        return "\n".join(lines)

    def _generate_countdown_script(self, topic: str, manifest: dict) -> dict:
        prompt = SCRIPT_PROMPT_TEMPLATE.format(topic=topic, manifest_block=self._manifest_block(manifest))

        last_error = None
        for attempt in range(1, MAX_SCRIPT_RETRIES + 1):
            try:
                script = self.call_gemini_json(prompt, attempts=1)

                # The assembly step maps segments to clip files purely by
                # this number -- a malformed or duplicated set would
                # silently pair the wrong narration with the wrong footage.
                numbers = sorted(seg.get("clip_number") for seg in script.get("segments", []))
                if numbers != [1, 2, 3, 4, 5]:
                    raise ValueError(f"bad clip_number set: {numbers}")

                score, breakdown, issues = score_script(script)
                print(f"RankedNiche quality gate attempt {attempt}/{MAX_SCRIPT_RETRIES}: {score}/100 {breakdown}")
                if score >= QUALITY_THRESHOLD:
                    return script
                raise ValueError(f"below quality threshold ({QUALITY_THRESHOLD}): {issues}")

            except Exception as e:
                last_error = e
                print(f"RankedNiche script attempt {attempt}/{MAX_SCRIPT_RETRIES} failed: {e}")
                if attempt < MAX_SCRIPT_RETRIES:
                    time.sleep(5 * attempt)

        raise RuntimeError(f"RankedNiche script generation failed after {MAX_SCRIPT_RETRIES} attempts: {last_error}")

    # =========================================================
    # RankedNiche-only: sound effect placement
    # =========================================================

    def _load_sfx_catalog(self) -> list[_SfxCatalogEntry]:
        """Reads assets/rankedniche/sfx/catalog.json -- a human-authored
        list of available sound effects and when each one fits. Entries
        with a missing audio file are dropped with a warning. Returns []
        (not an error) if there's no catalog at all -- SFX are optional."""
        catalog_path = self.sfx_dir / CATALOG_FILENAME
        if not catalog_path.exists():
            print(f"No SFX catalog at {catalog_path} -- proceeding without sound effects.")
            return []

        raw = json.loads(catalog_path.read_text())
        entries = []
        for item in raw:
            file_path = self.sfx_dir / item["file"]
            if not file_path.exists():
                print(f"SFX catalog entry '{item['id']}' points to missing file {file_path} -- skipping.")
                continue
            entries.append(_SfxCatalogEntry(id=item["id"], file=item["file"], description=item["description"]))
        return entries

    def _pick_sfx_placements(self, script: dict, catalog: list[_SfxCatalogEntry]) -> list[dict]:
        """A second, separate Gemini call (kept separate from script
        generation for the same reason topic-picking is its own call in
        Auto: a focused prompt with a narrow output schema is far more
        reliable than asking one call to do two jobs). The model only
        picks WHICH sfx goes WHERE via a verbatim trigger phrase -- it
        never sees or produces timestamps."""
        catalog_block = "\n".join(f"- {e.id}: {e.description}" for e in catalog)
        segments_block = "\n".join(f"[{i}] {seg['narration']}" for i, seg in enumerate(script["segments"]))

        prompt = f"""Here is a video's narration, broken into segments by index:

{segments_block}

Available sound effects:
{catalog_block}

For each moment where a sound effect would genuinely land well (most
segments won't need one), pick the effect and a short trigger phrase:
3 to 6 words copied VERBATIM from that segment's narration, marking
exactly where the effect should play.

Return ONLY a JSON array, no other text:
[{{"segment_index": 0, "sfx_id": "...", "trigger_phrase": "..."}}, ...]
If nothing fits anywhere, return []."""

        raw_events = self.call_gemini_json(prompt, attempts=1)
        if not isinstance(raw_events, list):
            print(f"SFX placement call returned non-list ({type(raw_events)}) -- skipping SFX this run.")
            return []

        catalog_ids = {e.id for e in catalog}
        valid_events = []
        for event in raw_events:
            seg_idx = event.get("segment_index")
            sfx_id = event.get("sfx_id")
            phrase = event.get("trigger_phrase", "")

            if sfx_id not in catalog_ids:
                print(f"SFX placement referenced unknown sfx_id '{sfx_id}' -- skipping.")
                continue
            if not isinstance(seg_idx, int) or not (0 <= seg_idx < len(script["segments"])):
                print(f"SFX placement referenced invalid segment_index {seg_idx!r} -- skipping.")
                continue
            narration = script["segments"][seg_idx]["narration"]
            if phrase.lower() not in narration.lower():
                print(f"SFX trigger phrase {phrase!r} not found verbatim in segment {seg_idx} -- skipping.")
                continue

            valid_events.append({"segment_index": seg_idx, "sfx_id": sfx_id, "trigger_phrase": phrase})

        return valid_events

    def _normalize(self, text: str) -> str:
        return re.sub(r"[^\w\s]", "", text).lower()

    def _find_phrase_start(self, words: list[tuple[str, float, float]], phrase: str) -> float | None:
        """Best-effort fuzzy locate: whisper transcribes the actual TTS
        audio, while the trigger phrase comes from the script text fed to
        the LLM -- they usually match closely but not always character-
        for-character. Slides a window the length of the phrase across
        the transcribed words, scores each by token overlap, returns the
        best window's start time. Returns None if nothing overlaps enough
        to trust -- better to skip an SFX than place it in the wrong spot."""
        phrase_tokens = self._normalize(phrase).split()
        word_tokens = [self._normalize(w[0]) for w in words]
        if not phrase_tokens or not word_tokens:
            return None

        n = len(phrase_tokens)
        best_idx, best_score = None, 0.0
        for i in range(len(word_tokens)):
            window = word_tokens[i : i + n]
            if not window:
                continue
            overlap = len(set(window) & set(phrase_tokens))
            score = overlap / n
            if score > best_score:
                best_score, best_idx = score, i

        if best_idx is None or best_score < MIN_SFX_MATCH_OVERLAP:
            return None
        return words[best_idx][1]

    def _resolve_sfx_timestamps(self, events: list[dict], catalog: list[_SfxCatalogEntry]) -> list[_SfxEvent]:
        catalog_by_id = {e.id: e for e in catalog}
        resolved = []
        for event in events:
            seg_idx = event["segment_index"]
            words = self._segment_words[seg_idx]
            local_start = self._find_phrase_start(words, event["trigger_phrase"])
            if local_start is None:
                print(f"Could not locate trigger phrase {event['trigger_phrase']!r} in segment "
                      f"{seg_idx}'s transcribed audio -- skipping this SFX.")
                continue

            entry = catalog_by_id[event["sfx_id"]]
            resolved.append(_SfxEvent(
                segment_index=seg_idx, sfx_id=entry.id, sfx_path=self.sfx_dir / entry.file,
                timestamp_seconds=self._segment_offsets[seg_idx] + local_start,
            ))
        return resolved

    def mix_sfx(self, video_path: Path, events: list[tuple[Path, float]], out_path: Path,
                sfx_volume: float = 0.9) -> None:
        """Overlays each (sfx_path, absolute_timestamp) onto the already-
        concatenated video's audio. adelay uses all=1 so it works
        regardless of whether a given SFX file is mono or stereo. amix
        uses duration=longest so a late-placed SFX doesn't get truncated
        by the narration track ending first; -shortest on the final
        output then caps everything back to the video's own length."""
        if not events:
            subprocess.run(["ffmpeg", "-y", "-i", str(video_path), "-c", "copy", str(out_path)], check=True)
            return

        inputs = ["-i", str(video_path)]
        for sfx_path, _ in events:
            inputs += ["-i", str(sfx_path)]

        filter_parts = []
        mix_labels = ["0:a"]
        for i, (_, timestamp) in enumerate(events, start=1):
            delay_ms = max(0, round(timestamp * 1000))
            filter_parts.append(f"[{i}:a]adelay={delay_ms}:all=1,volume={sfx_volume}[sfx{i}]")
            mix_labels.append(f"sfx{i}")

        mix_inputs = "".join(f"[{label}]" for label in mix_labels)
        filter_parts.append(f"{mix_inputs}amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=0[a]")
        filter_complex = ";".join(filter_parts)

        subprocess.run(
            ["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex,
             "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", str(out_path)],
            check=True,
        )