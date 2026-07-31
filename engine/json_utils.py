"""JSON extraction/repair for LLM responses that don't come back as clean
JSON (markdown fences, stray commentary, trailing commas, smart quotes).
Shared by channel script generation since the failure modes are
identical regardless of which model or prompt produced the text."""

import json
import re


def repair_json(blob: str) -> str:
    """Fix common LLM JSON mistakes that otherwise fail json.loads."""
    blob = blob.replace("\u201c", '"').replace("\u201d", '"')
    blob = blob.replace("\u2018", "'").replace("\u2019", "'")
    # Trailing commas before } or ]
    blob = re.sub(r",\s*([}\]])", r"\1", blob)
    return blob


def extract_json(text: str | None) -> dict:
    """Strips markdown fences and any stray commentary around a JSON
    object before parsing. Mainly needed for fallback/less-reliable models
    that don't respect "return only JSON" as strictly as Gemini's
    structured output mode does."""
    if not text or not text.strip():
        raise ValueError("Model returned empty content; expected a JSON object")

    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text[3:]
        text = text.lstrip()
        if text.lower().startswith("json"):
            text = text[4:]

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise ValueError(f"No JSON object found in model response: {text[:200]!r}")

    blob = repair_json(text[start : end + 1])
    try:
        return json.loads(blob)
    except json.JSONDecodeError as e:
        snippet_start = max(0, e.pos - 60)
        snippet = blob[snippet_start : e.pos + 60]
        raise json.JSONDecodeError(f"{e.msg} near: {snippet!r}", blob, e.pos) from None
