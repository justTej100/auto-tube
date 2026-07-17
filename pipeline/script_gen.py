"""Script generation. Gemini is the primary model; if it's unavailable
(rate limited, high-demand 503s, outage) after a few retries, falls back
to a free Hugging Face-hosted model so a Google outage doesn't kill the
whole run. The fallback only activates if HF_TOKEN is set -- if it's not,
behavior is unchanged from before (Gemini errors just raise)."""

import json
import time

from google import genai
from google.genai import types
from huggingface_hub import InferenceClient

from pipeline.quality import QUALITY_THRESHOLD, score_script

MAX_QUALITY_RETRIES = 3

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
}}"""


def _extract_json(text: str) -> dict:
    """Fallback models are less reliable about 'return only JSON' than
    Gemini's structured output mode -- strips markdown fences and any
    stray commentary around the JSON object before parsing."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    return json.loads(text[start:end + 1])


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


def _generate_with_huggingface(hf_token: str, topic: str) -> dict:
    client = InferenceClient(api_key=hf_token)
    prompt = PROMPT_TEMPLATE.format(topic=topic) + "\n\nReturn ONLY the JSON object, no other text."

    completion = client.chat.completions.create(
        model="meta-llama/Llama-3.1-8B-Instruct",
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_json(completion.choices[0].message.content)


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
