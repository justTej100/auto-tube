"""Trending-topic research: combines Gemini's live Google Search grounding
with Reddit's public .json endpoints to surface real current topics,
instead of writing about a generic invented fact.

Google Search grounding gives 5,000 free grounded prompts/month on
Gemini 3.x models -- a couple of calls a day here is nowhere near that."""

from dataclasses import dataclass

import requests
from google import genai
from google.genai import types

REDDIT_HEADERS = {"User-Agent": "dailydose-research-bot/1.0"}
REDDIT_URL = "https://www.reddit.com/r/all/top.json?t=day&limit=10"


@dataclass(frozen=True)
class TopicResearch:
    """Picked topic plus the raw research brief fed into script writing."""
    topic: str
    research_context: str


def fetch_reddit_trending() -> list[str]:
    """Pulls today's top post titles from r/all -- a raw, unfiltered
    signal of what people are actually talking about right now. Fails
    soft (empty list) so a Reddit hiccup doesn't kill the whole run."""
    try:
        r = requests.get(REDDIT_URL, headers=REDDIT_HEADERS, timeout=15)
        r.raise_for_status()
        posts = r.json()["data"]["children"]
        return [p["data"]["title"] for p in posts if not p["data"].get("over_18")]
    except Exception as e:
        print(f"Reddit trending fetch failed ({e}) -- continuing without it.")
        return []


def fetch_gemini_trending(gemini_api_key: str) -> str:
    """Asks Gemini, grounded in live Google Search, what's trending right
    now that would make a compelling short-video story. Fails soft so a
    grounding outage can still fall back to Reddit-only research."""
    try:
        client = genai.Client(api_key=gemini_api_key)
        search_tool = types.Tool(google_search=types.GoogleSearch())

        prompt = (
            "Search for what's trending in news, culture, and social media "
            "right now. List 5 specific current stories or topics (not vague "
            "categories) that would make a compelling short story-driven video "
            "for a general 'daily dose of life' YouTube audience. One line each."
        )
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt,
            config=types.GenerateContentConfig(tools=[search_tool]),
        )
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("Gemini grounded search returned empty text")
        return text
    except Exception as e:
        print(f"Gemini grounded search failed ({e}) -- continuing without it.")
        return ""


def pick_todays_topic(gemini_api_key: str) -> TopicResearch:
    """Combines both sources into a topic + research brief for
    generate_script(). Two Gemini calls total (grounded search, then a
    plain pick-the-best call) -- well within free-tier daily limits."""
    reddit_titles = fetch_reddit_trending()
    gemini_trending = fetch_gemini_trending(gemini_api_key)

    reddit_block = "\n".join(f"- {t}" for t in reddit_titles[:10]) or "(unavailable)"
    web_block = gemini_trending or "(unavailable)"

    if reddit_block == "(unavailable)" and web_block == "(unavailable)":
        raise RuntimeError(
            "Trending research failed: both Reddit and Gemini grounded search "
            "were unavailable. Set VIDEO_TOPIC to override, or retry later."
        )

    research_context = (
        f"Currently trending on Reddit today:\n{reddit_block}\n\n"
        f"Currently trending per live web search:\n{web_block}"
    )

    combined_prompt = (
        f"{research_context}\n\n"
        "Pick the SINGLE most compelling, story-worthy topic from the above "
        "for a short narrative YouTube video. Describe it in one sentence, "
        "specific enough to write a script from -- not a vague category. "
        "Reply with ONLY that one sentence."
    )

    client = genai.Client(api_key=gemini_api_key)
    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=combined_prompt,
    )
    topic = (response.text or "").strip()
    if not topic:
        raise RuntimeError("Topic picker returned empty text")

    return TopicResearch(topic=topic, research_context=research_context)
