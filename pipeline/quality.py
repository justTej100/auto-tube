"""Post-generation quality gate for narration scripts, adapted from
content-ops's content-quality-scorer.py (github.com/ericosiu/ai-marketing-skills).

Only 3 of their 5 original scoring dimensions are ported here --
voice_similarity and engagement_potential are tuned for social-post/founder
voice ("$47K", "Follow for more", "What's your take?") and don't belong in
spoken video narration. slop_penalty, specificity, and length are
genre-agnostic and transfer cleanly.

Pure regex/heuristic scoring, no LLM call -- free, no API cost."""

import math
import re

# Banned AI words -- penalized in scoring. Ported as-is from content-ops.
BANNED_WORDS = [
    "leverage", "synergy", "ecosystem", "holistic", "at the end of the day",
    "delve", "tapestry", "landscape", "multifaceted", "nuanced", "pivotal",
    "realm", "robust", "seamless", "testament", "transformative", "underscore",
    "utilize", "whilst", "keen", "embark", "comprehensive", "intricate",
    "commendable", "meticulous", "paramount", "groundbreaking", "innovative",
    "cutting-edge", "paradigm", "Additionally", "crucial", "enduring",
    "enhance", "fostering", "garner", "highlight", "interplay", "intricacies",
    "showcase", "vibrant", "valuable", "profound", "renowned", "breathtaking",
    "nestled", "stunning", "I'm excited to share", "I think maybe",
    "It could potentially", "dive into", "game-changer", "unlock",
]

# AI-writing patterns to detect. Ported as-is from content-ops.
AI_PATTERNS = [
    (r"pivotal moment|is a testament|stands as", "significance_inflation"),
    (r"boasts|vibrant|commitment to", "promotional_language"),
    (r"experts believe|industry reports|studies show", "vague_attribution"),
    (r"despite.{1,50}continues to", "formulaic_structure"),
    (r"serves as|acts as|functions as", "copula_avoidance"),
    (r"it's not just .{1,30}, it's", "negative_parallelism"),
    (r"could potentially|might possibly|may perhaps", "excessive_hedging"),
    (r"the future looks bright|exciting times ahead|stay tuned", "generic_conclusion"),
]

# youtube_short limits from content-ops's PLATFORM_LIMITS -- fits our
# narration length well (our scripts run roughly 600-900 characters).
LENGTH_MIN, LENGTH_MAX = 100, 800
LENGTH_OPTIMAL_MIN, LENGTH_OPTIMAL_MAX = 200, 600

QUALITY_THRESHOLD = 60
WEIGHTS = {"slop_penalty": 0.5, "specificity": 0.3, "length": 0.2}


def score_slop_penalty(text: str) -> tuple[float, list[str]]:
    """0-100, higher = less AI slop."""
    score = 100
    issues = []
    text_lower = text.lower()

    banned_found = [w for w in BANNED_WORDS if w.lower() in text_lower]
    if banned_found:
        score -= 10 * len(banned_found)
        issues.append(f"Banned words: {', '.join(banned_found[:3])}")

    ai_found = []
    for pattern, name in AI_PATTERNS:
        if re.findall(pattern, text, re.IGNORECASE):
            ai_found.append(name)
            score -= 8
    if ai_found:
        issues.append(f"AI patterns: {', '.join(ai_found[:3])}")

    em_dash_count = text.count("—")
    word_count = len(text.split())
    if em_dash_count > word_count / 200:
        score -= 5
        issues.append("Excessive em dash usage")

    corporate_patterns = [
        r"I'm excited to share", r"it is important to note", r"in order to",
        r"we are pleased to announce", r"stay tuned for",
    ]
    for pattern in corporate_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            score -= 15
            issues.append("Corporate speak detected")
            break

    return max(score, 0), issues


def score_specificity(text: str) -> float:
    """0-100, higher = more concrete facts/numbers/named entities."""
    score = 0
    number_patterns = [
        r"\$[\d,]+[KkMmBb]?(?:\+)?", r"\d+%", r"\d+x",
        r"\d+[\.,]?\d*\s*(?:hours?|minutes?|days?|weeks?|months?|years?)",
        r"\d+\s*(?:feet|meters?|miles?|pounds?|kilograms?|degrees?|species|years?)",
    ]
    total_numbers = sum(len(re.findall(p, text, re.IGNORECASE)) for p in number_patterns)
    word_count = len(text.split())
    number_density = total_numbers / max(word_count / 50, 1)
    score += min(number_density * 30, 50)

    entity_patterns = [r"[A-Z][a-z]+ [A-Z][a-z]+(?:\s[A-Z][a-z]+)*"]
    entity_count = sum(len(re.findall(p, text)) for p in entity_patterns)
    score += min(entity_count * 10, 50)

    return min(score, 100)


def score_length(text: str) -> float:
    """0-100, higher = better fit for a short-video narration length."""
    char_count = len(text)
    if char_count < LENGTH_MIN:
        return max((char_count / LENGTH_MIN) * 100, 20)
    elif char_count > LENGTH_MAX:
        return max((LENGTH_MAX / char_count) * 100, 30)
    elif LENGTH_OPTIMAL_MIN <= char_count <= LENGTH_OPTIMAL_MAX:
        return 100
    else:
        return 85


def score_script(script: dict) -> tuple[float, dict, list[str]]:
    """Scores a generated script dict (as returned by generate_script).
    Returns (weighted_total_0_100, per_dimension_scores, issues_list)."""
    full_text = " ".join(seg["narration"] for seg in script["segments"])

    slop_score, slop_issues = score_slop_penalty(full_text)
    specificity_score = score_specificity(full_text)
    length_score = score_length(full_text)

    breakdown = {
        "slop_penalty": round(slop_score, 1),
        "specificity": round(specificity_score, 1),
        "length": round(length_score, 1),
    }
    total = sum(breakdown[k] * WEIGHTS[k] for k in WEIGHTS)

    return round(total, 1), breakdown, slop_issues
