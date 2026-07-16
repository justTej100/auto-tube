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

PROMPT_TEMPLATE = """Write a short YouTube video script about: {topic}

Return JSON in this exact shape:
{{
  "title": "...",
  "description": "...",
  "segments": [
    {{"narration": "one or two sentences", "image_query": "2-4 word stock photo search"}},
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


def generate_script(gemini_api_key: str, topic: str, hf_token: str | None = None) -> dict:
    try:
        return _generate_with_gemini(gemini_api_key, topic)
    except Exception as e:
        if not hf_token:
            raise
        print(f"Gemini unavailable after retries ({e}). Falling back to Hugging Face.")
        return _generate_with_huggingface(hf_token, topic)
