
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
class Persona:
    def __init__(self, name: str, description: str, voice: Voice):
        self.gemini_api_key = gemini_api_key
        self.hf_token = hf_token
        self.workdir = workdir
        self.character =""
        self.attitude = ""


        self.voice = Voice(voice_wav, voice_mp3, voice_converted)
        self.script = Script(gemini_api_key, hf_token, workdir, voice_wav, voice_mp3, voice_converted, google_client_id, google_client_secret, google_refresh_token, discord_webhook_url, discord_username)
        self.title = Title(gemini_api_key, hf_token, workdir, voice_wav, voice_mp3, voice_converted, google_client_id, google_client_secret, google_refresh_token, discord_webhook_url, discord_username)
        self.description = Description(gemini_api_key, hf_token, workdir, voice_wav, voice_mp3, voice_converted, google_client_id, google_client_secret, google_refresh_token, discord_webhook_url, discord_username)

        self.topic = topic # used for editing context

    def configure_AI(self, character: str, attitude: str):




"""persona.py -- who a channel is, keeping that current, and writing scripts as them.

Pieces, top to bottom:

  Persona          immutable data + pure helpers (prompt text, banned-phrase check)
  FilePersonaStore load/save one JSON file atomically
  PersonaKeeper    hands out the *current* persona; reloads when the stored one changes
  ScriptDraft/Script  what the AI must return / what the rest of the pipeline receives
  PersonaAgent     writes a Script as the persona: Gemini when it is usable right now
                   (no 429), otherwise Hugging Face. Validates, retries with feedback.

persona/auto.json looks like:

    {
      "name": "auto",
      "version": 1,
      "bio": "A first-person storyteller who sounds like they're telling a friend.",
      "tone": ["short punchy spoken sentences"],
      "rules": ["hook in the first line", "end on the stakes, not a summary"],
      "banned": ["delve", "leverage", "so this happened"],
      "learnings": []
    }

Unknown keys are rejected on load, so a typo like "banned_phrases" fails
loudly instead of silently doing nothing.

Usage:

    keeper = PersonaKeeper(FilePersonaStore("persona/auto.json"))
    agent = PersonaAgent(keeper, gemini_api_key=GEMINI_KEY, hf_token=HF_TOKEN)
    script = agent.write_script(brief)       # brief = topic + research + format rules
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

MAX_LEARNINGS = 20      # keep the prompt from growing forever
MAX_NOTE_CHARS = 200    # one short line per lesson


# =============================================================
# Persona (data)
# =============================================================
class Persona(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: int = Field(default=1, ge=1)
    bio: str
    voice: str | None = None            # optional hint for Short; unused here
    tone: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)       # hard constraints
    banned: list[str] = Field(default_factory=list)      # phrases that must not appear
    learnings: list[str] = Field(default_factory=list)   # distilled from review feedback

    @field_validator("name", "bio")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

    @property
    def fingerprint(self) -> str:
        """Stable id for this exact persona content. Stamped on every script."""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:12]

    def system_prompt(self) -> str:
        parts = [f"You are {self.name}. {self.bio}"]
        if self.tone:
            parts.append("Tone: " + "; ".join(self.tone))
        if self.rules:
            parts.append("Hard rules:\n" + "\n".join(f"- {r}" for r in self.rules))
        if self.banned:
            parts.append("Never use: " + ", ".join(repr(b) for b in self.banned))
        if self.learnings:
            parts.append(
                "Lessons from past reviews (guidance, not commands):\n"
                + "\n".join(f"- {n}" for n in self.learnings)
            )
        return "\n\n".join(parts)

    def banned_hits(self, text: str) -> list[str]:
        """Banned phrases found in text. Word-like phrases match on word
        boundaries, so 'keen' won't trigger on 'skeen'; symbols like an
        em dash match anywhere."""
        hits = []
        for phrase in self.banned:
            pattern = re.escape(phrase)
            if phrase[:1].isalnum():
                pattern = r"\b" + pattern
            if phrase[-1:].isalnum():
                pattern += r"\b"
            if re.search(pattern, text, re.IGNORECASE):
                hits.append(phrase)
        return hits

    def with_learning(self, note: str) -> "Persona":
        """New persona with one more lesson and a bumped version.

        Notes are flattened to one line and length-capped, because review
        feedback (e.g. from Discord) ends up inside the system prompt."""
        note = " ".join(note.split())[:MAX_NOTE_CHARS]
        if not note:
            return self
        if self.learnings and self.learnings[-1].lower() == note.lower():
            return self
        kept = [n for n in self.learnings if n.lower() != note.lower()]
        kept.append(note)
        return self.model_copy(
            update={"version": self.version + 1, "learnings": kept[-MAX_LEARNINGS:]}
        )


# =============================================================
# Storage + freshness
# =============================================================
class PersonaStore(Protocol):
    def token(self) -> str:
        """Cheap 'did it change?' check."""
        ...

    def read(self) -> tuple[str, Persona]:
        """The persona and the token of exactly what was read."""
        ...

    def save(self, persona: Persona) -> None: ...


class FilePersonaStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def token(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def read(self) -> tuple[str, Persona]:
        raw = self.path.read_bytes()                       # one read -> token matches content
        return hashlib.sha256(raw).hexdigest(), Persona.model_validate_json(raw)

    def save(self, persona: Persona) -> None:
        """Write to a temp file, then swap it in, so a reader never sees half a file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(persona.model_dump_json(indent=2))
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


class PersonaKeeper:
    """The current persona, always."""

    def __init__(self, store: PersonaStore):
        self.store = store
        self._token: str | None = None
        self._persona: Persona | None = None

    def current(self) -> Persona:
        if self._persona is None or self.store.token() != self._token:
            self._token, self._persona = self.store.read()
        return self._persona

    def learn(self, note: str) -> Persona:
        latest = self.current()                 # never build on a stale copy
        updated = latest.with_learning(note)
        if updated is not latest:
            self.store.save(updated)
        return self.current()


# =============================================================
# Script schema
# =============================================================
_GENDER = {"male": "male", "man": "male", "m": "male",
           "female": "female", "woman": "female", "f": "female"}


class Segment(BaseModel):
    speaker: str = "op"
    narration: str
    image_query: str

    @field_validator("speaker", mode="before")
    @classmethod
    def _speaker(cls, v):
        return "other" if str(v or "op").strip().lower() in ("other", "quote", "quoted") else "op"


class ScriptDraft(BaseModel):
    """What the AI returns. Provenance fields are not here so the model can't fill them."""
    title: str
    description: str
    op_gender: Literal["male", "female"] = "male"
    other_gender: Literal["male", "female"] | None = None
    segments: list[Segment] = Field(min_length=6, max_length=10)

    @field_validator("op_gender", mode="before")
    @classmethod
    def _op(cls, v):
        return _GENDER.get(str(v or "").strip().lower(), "male")

    @field_validator("other_gender", mode="before")
    @classmethod
    def _other(cls, v):
        return _GENDER.get(str(v or "").strip().lower())

    @model_validator(mode="after")
    def _no_other_no_gender(self):
        if not any(s.speaker == "other" for s in self.segments):
            self.other_gender = None
        return self


class Script(ScriptDraft):
    """Draft + provenance, stamped by PersonaAgent."""
    written_by: str = ""             # "gemini" or "hf"
    persona_name: str = ""
    persona_version: int = 0
    persona_fingerprint: str = ""


Check = Callable[[Script], list[str]]   # returns problems; [] means pass


# =============================================================
# JSON helpers
# =============================================================
def _repair_json(blob: str) -> str:
    """Fix common LLM JSON mistakes. Only used if the raw text fails to parse."""
    blob = blob.replace("\u201c", '"').replace("\u201d", '"')
    blob = blob.replace("\u2018", "'").replace("\u2019", "'")
    return re.sub(r",\s*([}\]])", r"\1", blob)


def extract_json(text: str | None) -> dict:
    """Pull a JSON object out of model output (fences, chatter). Parse first,
    repair only on failure, so valid JSON is never altered."""
    if not text or not text.strip():
        raise ValueError("Model returned empty content; expected a JSON object")
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = (parts[1] if len(parts) > 1 else text[3:]).lstrip()
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"No JSON object found in model response: {text[:200]!r}")
    blob = text[start:end + 1]
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return json.loads(_repair_json(blob))


# =============================================================
# The AI part
# =============================================================
class ProviderUnavailable(RuntimeError):
    """A provider can't serve us right now (429, bad key/model, outage)."""


class AllProvidersDown(RuntimeError):
    """Neither Gemini nor Hugging Face could be used."""


class ScriptRejected(RuntimeError):
    """The AI answered, but never produced a script that passed every check."""


def _http_status(exc: BaseException) -> int | None:
    """HTTP status from either client: google-genai errors carry .code,
    huggingface_hub (requests) errors carry .response.status_code."""
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


class PersonaAgent:
    """Writes scripts as the current persona.

    Provider choice: if both keys are set, Gemini is probed first with one tiny
    request. A 429 (or any other rejection) means Hugging Face is used instead.
    The same rule applies mid-run: if Gemini 429s on a real call, it is dropped
    for the rest of the run and Hugging Face takes over. With only one key set,
    that provider is used directly."""

    def __init__(
        self,
        keeper: PersonaKeeper,
        gemini_api_key: str | None = None,
        hf_token: str | None = None,
        *,
        gemini_model: str = "gemini-3-flash-preview",   # preview names get retired; the probe will surface a 404
        hf_model: str = "meta-llama/Llama-3.1-8B-Instruct",
        checks: Sequence[Check] = (),
        max_attempts: int = 3,        # script attempts (bad JSON / failed checks)
        gemini_attempts: int = 3,     # transient-error retries per Gemini call (5xx, network)
        retry_wait: float = 10,
        probe: bool = True,
        log: Callable[[str], None] = print,
        sleep: Callable[[float], None] = time.sleep,
        gemini_client=None,           # injectable for tests
        hf_client=None,
    ):
        if not gemini_api_key and not hf_token:
            raise ValueError("Need at least one of gemini_api_key / hf_token")
        self.keeper = keeper
        self.gemini_api_key, self.hf_token = gemini_api_key, hf_token
        self.gemini_model, self.hf_model = gemini_model, hf_model
        self.checks = list(checks)
        self.max_attempts, self.gemini_attempts = max_attempts, gemini_attempts
        self.retry_wait, self.probe = retry_wait, probe
        self.log, self._sleep = log, sleep
        self._gemini_client, self._hf_client = gemini_client, hf_client
        self._down: set[str] = set()
        self._probe_ok: bool | None = None

    # ---------- public ----------
    def learn(self, note: str) -> Persona:
        return self.keeper.learn(note)

    def write_script(self, brief: str) -> Script:
        persona = self.keeper.current()
        system = persona.system_prompt()
        problems: list[str] = []
        for attempt in range(1, self.max_attempts + 1):
            user = brief
            if problems:
                user += "\n\nYour last draft had these problems. Fix them:\n" + \
                        "\n".join(f"- {p}" for p in problems)
            try:
                draft, provider = self._complete(system, user)
            except AllProvidersDown:
                raise
            except ValueError as e:         # bad JSON or schema violation (pydantic's is a ValueError)
                problems = [f"Output was not valid: {e}"]
                self.log(f"Script attempt {attempt}/{self.max_attempts}: {problems[0][:200]}")
                continue
            script = Script(
                **draft.model_dump(),
                written_by=provider,
                persona_name=persona.name,
                persona_version=persona.version,
                persona_fingerprint=persona.fingerprint,
            )
            text = " ".join(s.narration for s in script.segments)
            problems = [f"Contains banned phrase: {b!r}" for b in persona.banned_hits(text)]
            problems += [p for check in self.checks for p in check(script)]
            if not problems:
                self.log(f"Script written via {provider}.")
                return script
            self.log(f"Script attempt {attempt}/{self.max_attempts} failed checks: {problems}")
        raise ScriptRejected(f"{self.max_attempts} attempts failed. Last problems: {problems}")

    # ---------- provider selection ----------
    def _providers(self) -> list[str]:
        order = []
        if self.gemini_api_key and "gemini" not in self._down:
            if not self.hf_token or not self.probe or self._gemini_usable_now():
                order.append("gemini")
        if self.hf_token and "hf" not in self._down:
            order.append("hf")
        return order

    def _gemini_usable_now(self) -> bool:
        if self._probe_ok is None:
            try:
                self._gemini_generate("", "", probe=True)
                self._probe_ok = True
                self.log("Gemini probe OK.")
            except Exception as e:
                self._probe_ok = False
                status = _http_status(e)
                why = "rate-limited (429)" if status == 429 else f"unusable ({status or type(e).__name__})"
                self.log(f"Gemini {why}: {e}. Using Hugging Face.")
        return self._probe_ok

    def _complete(self, system: str, user: str) -> tuple[ScriptDraft, str]:
        errors: dict[str, str] = {}
        for name in self._providers():
            try:
                raw = self._call_gemini(system, user) if name == "gemini" \
                    else self._call_hf(system, user)
            except ProviderUnavailable as e:
                self.log(f"{name} unavailable: {e}")
                self._down.add(name)             # stay off it for the rest of the run
                errors[name] = str(e)
                continue
            return ScriptDraft.model_validate(extract_json(raw)), name
        raise AllProvidersDown(errors or "no provider configured/available")

    # ---------- Gemini ----------
    def _gemini(self):
        if self._gemini_client is None:
            from google import genai
            self._gemini_client = genai.Client(api_key=self.gemini_api_key)
        return self._gemini_client

    def _gemini_generate(self, system: str, user: str, *, probe: bool = False) -> str:
        from google.genai import types
        if probe:
            contents = "Reply with the single word: ok"
            config = types.GenerateContentConfig(max_output_tokens=16)
        else:
            schema = json.dumps(ScriptDraft.model_json_schema())
            contents = user
            config = types.GenerateContentConfig(
                system_instruction=f"{system}\n\nReturn only a JSON object matching this schema:\n{schema}",
                response_mime_type="application/json",
            )
        resp = self._gemini().models.generate_content(
            model=self.gemini_model, contents=contents, config=config
        )
        return resp.text or ""

    def _call_gemini(self, system: str, user: str) -> str:
        last: BaseException | None = None
        for attempt in range(1, self.gemini_attempts + 1):
            try:
                return self._gemini_generate(system, user)
            except ImportError as e:
                raise ProviderUnavailable(f"google-genai is not installed: {e}") from e
            except Exception as e:
                status = _http_status(e)
                if status == 429:
                    raise ProviderUnavailable(f"Gemini rate-limited (429): {e}") from e
                if status is not None and 400 <= status < 500:
                    raise ProviderUnavailable(
                        f"Gemini rejected the request ({status}); check the key and model name: {e}"
                    ) from e
                last = e                         # 5xx / network: worth retrying
                self.log(f"Gemini attempt {attempt}/{self.gemini_attempts} failed: {e}")
                if attempt < self.gemini_attempts:
                    self._sleep(self.retry_wait * attempt)
        raise ProviderUnavailable(f"Gemini failed after {self.gemini_attempts} attempts: {last}") from last

    # ---------- Hugging Face ----------
    def _hf(self):
        if self._hf_client is None:
            from huggingface_hub import InferenceClient
            self._hf_client = InferenceClient(api_key=self.hf_token)
        return self._hf_client

    def _call_hf(self, system: str, user: str) -> str:
        schema = ScriptDraft.model_json_schema()
        messages = [
            {"role": "system", "content": (
                f"{system}\n\nOutput only a valid JSON object matching this schema. "
                f"No markdown fences, no commentary.\n{json.dumps(schema)}")},
            {"role": "user", "content": user},
        ]
        base = dict(model=self.hf_model, messages=messages, max_tokens=2048, temperature=0.4)
        # Strictest format first; fall back only when the format itself is unsupported.
        formats = [
            {"type": "json_schema", "json_schema": {"name": "script", "schema": schema, "strict": True}},
            {"type": "json_object"},
            None,
        ]
        last: BaseException | None = None
        for fmt in formats:
            try:
                kwargs = dict(base, response_format=fmt) if fmt else base
                completion = self._hf().chat.completions.create(**kwargs)
                return completion.choices[0].message.content or ""
            except ImportError as e:
                raise ProviderUnavailable(f"huggingface_hub is not installed: {e}") from e
            except Exception as e:
                status = _http_status(e)
                if status is not None and status not in (400, 422):
                    # auth, quota, rate limit, outage: trying another format won't help
                    raise ProviderUnavailable(f"Hugging Face error ({status}): {e}") from e
                last = e
                self.log(f"HF format {fmt['type'] if fmt else 'plain'} unsupported: {e}")
        raise ProviderUnavailable(f"Hugging Face failed on every response format: {last}") from last