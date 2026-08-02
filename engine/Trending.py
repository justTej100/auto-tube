"""Parent for topic research. Subclasses (Reddit, News) implement gather()
and topic_pick_prompt(). pick_topic() tries Gemini then Hugging Face."""

from __future__ import annotations

from datetime import date

from google import genai
from huggingface_hub import InferenceClient

from engine.DiscordNotify import DiscordNotifier

USER_AGENT = "dailydose-research-bot/1.0 (github actions; contact: local)"
HTTP_TIMEOUT = 20


class SourceAttempt:
    def __init__(
        self,
        name: str,
        lines: list[str] | None = None,
        skipped: bool = False,
        reason: str = "",
    ) -> None:
        self.name = name
        self.lines = lines if lines is not None else []
        self.skipped = skipped
        self.reason = reason


class TopicResearch:
    def __init__(
        self,
        topic: str,
        research_context: str,
        skipped_sources: tuple[str, ...] = (),
        used_sources: tuple[str, ...] = (),
        research_findings: tuple[tuple[str, str], ...] = (),
        topic_picker: str = "Gemini",
    ) -> None:
        self.topic = topic
        self.research_context = research_context
        self.skipped_sources = skipped_sources
        self.used_sources = used_sources
        self.research_findings = research_findings
        self.topic_picker = topic_picker


def headers(**extra: str) -> dict[str, str]:
    h = {"User-Agent": USER_AGENT}
    h.update(extra)
    return h


def as_bullets(lines: list[str], limit: int = 10) -> str:
    return "\n".join(f"- {line}" for line in lines[:limit])


class Trending:
    def __init__(
        self,
        gemini_api_key: str,
        *,
        discord: DiscordNotifier,
        hf_token: str | None = None,
    ) -> None:
        self.gemini_api_key = gemini_api_key
        self.discord = discord
        self.hf_token = hf_token

    def gather(self) -> list[SourceAttempt]:
        raise NotImplementedError

    def topic_pick_prompt(self, research_context: str) -> str:
        raise NotImplementedError

    def pick_topic(self, research_context: str) -> tuple[str, str]:
        """Gemini first; fall back to Hugging Face. Returns (topic, picker)."""
        prompt = self.topic_pick_prompt(research_context)
        try:
            client = genai.Client(api_key=self.gemini_api_key)
            response = client.models.generate_content(
                model="gemini-3-flash-preview",
                contents=prompt,
            )
            topic = (response.text or "").strip()
            if not topic:
                raise RuntimeError("Gemini topic picker returned empty text")
            return topic, "Gemini"
        except Exception as e:
            if not self.hf_token:
                raise RuntimeError(
                    f"Topic picking failed and no HF_TOKEN set: {e}"
                ) from e
            print(f"Gemini topic pick failed ({e}). Falling back to Hugging Face.")
            client = InferenceClient(api_key=self.hf_token)
            completion = client.chat.completions.create(
                model="meta-llama/Llama-3.1-8B-Instruct",
                messages=[
                    {
                        "role": "system",
                        "content": "Reply with ONLY one sentence naming the chosen topic.",
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=120,
                temperature=0.4,
            )
            topic = (completion.choices[0].message.content or "").strip()
            if not topic:
                raise RuntimeError("HF topic picker returned empty text")
            return topic, "Hugging Face"

    def research(self) -> TopicResearch:
        attempts = self.gather()
        successful = [a for a in attempts if not a.skipped and a.lines]
        skipped = [a for a in attempts if a.skipped]

        if skipped:
            skip_lines = [f"**{a.name}**: {a.reason}" for a in skipped]
            try:
                self.discord.send_research_skip(skip_lines)
                print(f"Discord notified about {len(skipped)} skipped research source(s).")
            except Exception as e:
                print(f"Discord skip notification failed ({e})")

        if not successful:
            names = ", ".join(a.name for a in attempts)
            raise RuntimeError(
                f"Trending research failed: every source was unavailable ({names}). "
                "Set VIDEO_TOPIC to override, or retry later."
            )

        sections = [f"### {a.name}\n{as_bullets(a.lines)}" for a in successful]
        research_context = (
            f"Live research gathered {date.today().isoformat()} UTC:\n\n"
            + "\n\n".join(sections)
        )
        research_findings = tuple((a.name, a.lines[0]) for a in successful)
        topic, topic_picker = self.pick_topic(research_context)

        return TopicResearch(
            topic=topic,
            research_context=research_context,
            skipped_sources=tuple(a.name for a in skipped),
            used_sources=tuple(a.name for a in successful),
            research_findings=research_findings,
            topic_picker=topic_picker,
        )
