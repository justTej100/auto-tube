"""Script generation. Gemini is the primary model; if it's unavailable
(rate limited, high-demand 503s, outage) after a few retries, falls back
to a free Hugging Face-hosted model so a Google outage doesn't kill the
whole run. The fallback only activates if HF_TOKEN is set -- if it's not,
behavior is unchanged from before (Gemini errors just raise)."""

import json
import re
import time

from google import genai
from google.genai import types
from huggingface_hub import InferenceClient

from pipeline.quality import QUALITY_THRESHOLD, score_script

MAX_QUALITY_RETRIES = 3
MAX_HF_JSON_RETRIES = 3

SCRIPT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "narration": {"type": "string"},
                    "image_query": {"type": "string"},
                },
                "required": ["narration", "image_query"],
                "additionalProperties": False,
            },
            "minItems": 6,
            "maxItems": 10,
        },
    },
    "required": ["title", "description", "segments"],
    "additionalProperties": False,
}

PROMPT_TEMPLATE = """Write a script for a short, punchy YouTube video about: {topic}

You're writing for a fast-scrolling audience who will swipe away in 2
seconds if they're not hooked immediately. Follow these rules:

- Segment 1 MUST open with a surprising claim, a question, or a
  "you'd think X, but actually Y" twist -- never a boring setup line like
  "Did you know..." or "Today we're talking about...". Earn the next
  3 seconds.
- Vary sentence rhythm. Mix short punchy lines with longer ones. Avoid
  robotic uniform pacing where every segment is the same length and shape.
- Write like a person talking to a friend, not a Wikipedia summary. Use
  natural spoken phrasing, not formal written English.
- Avoid AI-writing tells: no "leverage", "delve", "landscape", "robust",
  "testament", "pivotal", "seamless", or similar corporate/AI vocabulary.
  No "it's not just X, it's Y" constructions. No vague "experts believe"
  attributions -- be specific or don't claim it.
- Include concrete numbers, measurements, or named specifics wherever
  possible instead of vague claims.
- End on a payoff, a twist, or a thought that lingers -- not a flat
  restatement of the topic.
- For each segment's image_query, describe a SPECIFIC, vivid, concrete
  visual that matches that exact sentence -- not the general topic. A
  generic query like "ocean water" returns generic stock photos. A
  specific query like "diver flashlight dark cave" returns something with
  actual visual interest. Think like a photo editor choosing an image for
  that exact moment, not a librarian tagging the general subject.

Return JSON in this exact shape:
{{
  "title": "...",
  "description": "...",
  "segments": [
    {{"narration": "one or two sentences", "image_query": "specific vivid visual, 3-6 words"}},
    ... (6 to 10 segments total)
  ]
}}

JSON rules: use double quotes only, no trailing commas, no comments,
and escape any double quotes inside string values as \\". """


def _repair_json(blob: str) -> str:
    """Fix common LLM JSON mistakes that otherwise fail json.loads."""
    blob = blob.replace("\u201c", '"').replace("\u201d", '"')
    blob = blob.replace("\u2018", "'").replace("\u2019", "'")
    # Trailing commas before } or ]
    blob = re.sub(r",\s*([}\]])", r"\1", blob)
    return blob


def _extract_json(text: str | None) -> dict:
    """Fallback models are less reliable about 'return only JSON' than
    Gemini's structured output mode -- strips markdown fences and any
    stray commentary around the JSON object before parsing."""
    if not text or not text.strip():
        raise ValueError("Model returned empty content; expected a JSON script object")

    text = text.strip()
    if text.startswith("```"):
        # Prefer the fenced block contents; fall back to stripping the opener.
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text[3:]
        text = text.lstrip()
        if text.lower().startswith("json"):
            text = text[4:]

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise ValueError(f"No JSON object found in model response: {text[:200]!r}")

    blob = _repair_json(text[start : end + 1])
    try:
        return json.loads(blob)
    except json.JSONDecodeError as e:
        snippet_start = max(0, e.pos - 60)
        snippet = blob[snippet_start : e.pos + 60]
        raise json.JSONDecodeError(
            f"{e.msg} near: {snippet!r}",
            blob,
            e.pos,
        ) from None


def _generate_with_gemini(api_key: str, topic: str, attempts: int = 3) -> dict:
    client = genai.Client(api_key=api_key)
    prompt = PROMPT_TEMPLATE.format(topic=topic)

    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            resp = client.models.generate_content(
                model="gemini-3-flash-preview",
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            return json.loads(resp.text)
        except Exception as e:
            last_error = e
            print(f"Gemini attempt {attempt}/{attempts} failed: {e}")
            if attempt < attempts:
                wait = 10 * attempt  # 10s, then 20s
                print(f"Retrying in {wait}s...")
                time.sleep(wait)
    raise last_error


def _hf_chat_json(client: InferenceClient, prompt: str) -> str:
    """Ask HF for JSON. Prefer structured output; fall back if unsupported."""
    messages = [
        {
            "role": "system",
            "content": (
                "You output only valid JSON objects that match the requested "
                "schema. No markdown fences, no commentary."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    kwargs = {
        "model": "meta-llama/Llama-3.1-8B-Instruct",
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0.4,
    }

    try:
        completion = client.chat.completions.create(
            **kwargs,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "youtube_script",
                    "schema": SCRIPT_JSON_SCHEMA,
                    "strict": True,
                },
            },
        )
    except Exception as e:
        print(f"HF json_schema response_format unsupported ({e}); trying json_object.")
        try:
            completion = client.chat.completions.create(
                **kwargs,
                response_format={"type": "json_object"},
            )
        except Exception as e2:
            print(f"HF json_object response_format unsupported ({e2}); plain chat.")
            completion = client.chat.completions.create(**kwargs)

    return completion.choices[0].message.content


def _generate_with_huggingface(hf_token: str, topic: str) -> dict:
    client = InferenceClient(api_key=hf_token)
    prompt = PROMPT_TEMPLATE.format(topic=topic) + "\n\nReturn ONLY the JSON object, no other text."

    last_error = None
    for attempt in range(1, MAX_HF_JSON_RETRIES + 1):
        try:
            content = _hf_chat_json(client, prompt)
            return _extract_json(content)
        except (json.JSONDecodeError, ValueError, TypeError, IndexError, KeyError) as e:
            last_error = e
            print(f"HF JSON parse attempt {attempt}/{MAX_HF_JSON_RETRIES} failed: {e}")
            if attempt < MAX_HF_JSON_RETRIES:
                time.sleep(2 * attempt)
    raise RuntimeError(
        f"Hugging Face fallback returned unparseable JSON after "
        f"{MAX_HF_JSON_RETRIES} attempts: {last_error}"
    ) from last_error


def _generate_once(gemini_api_key: str, topic: str, hf_token: str | None) -> dict:
    try:
        return _generate_with_gemini(gemini_api_key, topic)
    except Exception as e:
        if not hf_token:
            raise
        print(f"Gemini unavailable after retries ({e}). Falling back to Hugging Face.")
        return _generate_with_huggingface(hf_token, topic)


def generate_script(gemini_api_key: str, topic: str, hf_token: str | None = None) -> dict:
    """Generates a script, then runs it through the quality gate
    (pipeline/quality.py). Below-threshold scripts get regenerated up to
    MAX_QUALITY_RETRIES times before the run fails outright -- better to
    fail loudly than render a weak video."""
    last_score, last_breakdown, last_issues = None, None, None

    for attempt in range(1, MAX_QUALITY_RETRIES + 1):
        script = _generate_once(gemini_api_key, topic, hf_token)
        score, breakdown, issues = score_script(script)
        print(f"Quality gate attempt {attempt}/{MAX_QUALITY_RETRIES}: {score}/100 {breakdown}")

        if score >= QUALITY_THRESHOLD:
            return script

        print(f"Below threshold ({QUALITY_THRESHOLD}). Issues: {issues}")
        last_score, last_breakdown, last_issues = score, breakdown, issues

    raise RuntimeError(
        f"Script failed the quality gate after {MAX_QUALITY_RETRIES} attempts. "
        f"Last score: {last_score}/100 {last_breakdown}. Issues: {last_issues}"
    )
