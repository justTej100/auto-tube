import json

from google import genai
from google.genai import types


def generate_script(api_key: str, topic: str) -> dict:
    """Ask Gemini for a title, description, and narration segments, each
    paired with an image search query. Returns parsed JSON."""
    client = genai.Client(api_key=api_key)

    prompt = f"""Write a short YouTube video script about: {topic}

Return JSON in this exact shape:
{{
  "title": "...",
  "description": "...",
  "segments": [
    {{"narration": "one or two sentences", "image_query": "2-4 word stock photo search"}},
    ... (6 to 10 segments total)
  ]
}}"""

    resp = client.models.generate_content(
        model="gemini-3-flash",
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return json.loads(resp.text)
