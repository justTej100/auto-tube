import json

import anthropic


def generate_script(api_key: str, topic: str) -> dict:
    """Ask Claude for a title, description, and narration segments, each
    paired with an image search query. Returns parsed JSON."""
    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""Write a short YouTube video script about: {topic}

Return ONLY valid JSON, no markdown fences, no preamble, in this exact shape:
{{
  "title": "...",
  "description": "...",
  "segments": [
    {{"narration": "one or two sentences", "image_query": "2-4 word stock photo search"}},
    ... (6 to 10 segments total)
  ]
}}"""

    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    return json.loads(text)
