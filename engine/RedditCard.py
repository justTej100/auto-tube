"""Renders a small "Reddit post" card (avatar, username, title,
upvote/comment counts) as a transparent PNG sized to its own content.
It's meant to be composited on top of the first segment's stock footage
via Assemble.overlay_image -- not a separate clip -- so it appears
instantly over the footage/narration that's already playing instead of
holding up the video with a slide before it starts.

Used by Auto: the script's title becomes the post text, a random handle
stands in for the username, and the stats are randomized within a
realistic band -- this is a stylistic hook graphic, not a claim that a
real post said this, so nothing here should be presented as an authentic
screenshot of a specific real post/user.
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Overlay is sized well under the 1080-wide frame so the footage behind
# it stays visible around the edges.
CARD_WIDTH = 760

CARD_COLOR = (255, 255, 255, 255)
TITLE_COLOR = (10, 10, 10, 255)
META_COLOR = (120, 124, 126, 255)
FOOTER_COLOR = (120, 124, 126, 255)
AVATAR_PALETTE = [
    (255, 69, 0), (0, 121, 211), (70, 209, 128),
    (255, 140, 0), (149, 90, 232), (232, 90, 150),
]

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
FONT_BOLD = FONT_DIR / "DejaVuSans-Bold.ttf"
FONT_REGULAR = FONT_DIR / "DejaVuSans.ttf"

ADJECTIVES = [
    "quiet", "tired", "salty", "honest", "random", "lucky", "grumpy",
    "sleepy", "petty", "stubborn", "lowkey", "average", "nervous",
]
NOUNS = [
    "throwaway", "raccoon", "commuter", "hermit", "gremlin", "houseplant",
    "insomniac", "barista", "goblin", "wanderer", "typo", "pigeon",
]


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default()


def random_username(rng: random.Random) -> str:
    return f"u/{rng.choice(ADJECTIVES)}_{rng.choice(NOUNS)}{rng.randint(10, 98)}"


def format_count(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}K".replace(".0K", "K")
    return str(n)


def random_stats(rng: random.Random) -> tuple[str, str]:
    upvotes = rng.randint(8_000, 140_000)
    comments = int(upvotes * rng.uniform(0.15, 0.45))
    return format_count(upvotes), format_count(comments)


def _wrap_to_pixel_width(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int
) -> list[str]:
    """Greedy word-wrap measured against actual glyph widths (textwrap's
    character-count heuristic runs long with bold/condensed fonts)."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_avatar(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, letter: str, color: tuple) -> None:
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color + (255,))
    font = _font(FONT_BOLD, int(r * 1.1))
    bbox = draw.textbbox((0, 0), letter, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cx - w / 2 - bbox[0], cy - h / 2 - bbox[1]), letter, font=font, fill=(255, 255, 255, 255))


def _draw_upvote_icon(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: tuple) -> None:
    draw.polygon(
        [(x + size / 2, y), (x + size, y + size), (x, y + size)],
        fill=color + (255,),
    )


def _draw_bubble_icon(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: tuple) -> None:
    draw.rounded_rectangle(
        (x, y, x + size, y + size * 0.75), radius=size * 0.25, outline=color + (255,), width=5
    )
    draw.polygon(
        [
            (x + size * 0.25, y + size * 0.75),
            (x + size * 0.15, y + size * 1.0),
            (x + size * 0.45, y + size * 0.75),
        ],
        fill=color + (255,),
    )


def render_card_png(
    title: str,
    out_path: Path,
    username: str | None = None,
    subreddit: str | None = None,
    upvotes: str | None = None,
    comments: str | None = None,
    seed: int | None = None,
    max_lines: int = 4,
) -> Path:
    """Renders a small transparent card PNG sized to fit its own content
    (not the full frame) so it can be overlaid on top of playing footage.
    Any field left None is randomly generated. Title is clamped to
    max_lines so the overlay never grows large enough to cover the shot."""
    rng = random.Random(seed)
    username = username or random_username(rng)
    if upvotes is None or comments is None:
        upvotes, comments = random_stats(rng)

    # Small canvas just to measure text; real canvas sized after we know
    # how many lines the title wraps to.
    probe = Image.new("RGBA", (10, 10))
    probe_draw = ImageDraw.Draw(probe)

    title_font = _font(FONT_BOLD, 34)
    meta_font = _font(FONT_REGULAR, 22)
    footer_font = _font(FONT_BOLD, 24)

    padding = 28
    text_width_px = CARD_WIDTH - padding * 2
    wrapped = _wrap_to_pixel_width(probe_draw, title, title_font, text_width_px)
    if len(wrapped) > max_lines:
        wrapped = wrapped[:max_lines]
        last = wrapped[-1]
        while probe_draw.textlength(last + "...", font=title_font) > text_width_px and last:
            last = last[:-1].rstrip()
        wrapped[-1] = f"{last}..."

    line_height = int(title_font.size * 1.3)
    header_h = 66
    footer_h = 54
    body_h = line_height * len(wrapped) + 10
    card_h = header_h + body_h + footer_h + padding * 2

    img = Image.new("RGBA", (CARD_WIDTH, card_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, CARD_WIDTH, card_h), radius=22, fill=CARD_COLOR)

    # Header: avatar + username (+ subreddit line if given)
    avatar_r = 22
    cx = padding + avatar_r
    cy = padding + avatar_r
    color = rng.choice(AVATAR_PALETTE)
    _draw_avatar(draw, cx, cy, avatar_r, username[2:3].upper(), color)

    text_x = cx + avatar_r + 14
    if subreddit:
        draw.text((text_x, cy - 22), subreddit, font=_font(FONT_BOLD, 22), fill=TITLE_COLOR)
        draw.text((text_x, cy + 2), username, font=meta_font, fill=META_COLOR)
    else:
        draw.text((text_x, cy - meta_font.size / 2 - 4), username, font=_font(FONT_BOLD, 24), fill=TITLE_COLOR)

    # Body: wrapped title text
    y = padding + header_h
    for line in wrapped:
        draw.text((padding, y), line, font=title_font, fill=TITLE_COLOR)
        y += line_height

    # Footer: upvote + comment counts
    footer_y = card_h - padding - 30
    icon_size = 24
    fx = padding
    _draw_upvote_icon(draw, fx, footer_y, icon_size, (255, 69, 0))
    draw.text((fx + icon_size + 10, footer_y - 2), upvotes, font=footer_font, fill=FOOTER_COLOR)

    fx2 = fx + icon_size + 10 + draw.textlength(upvotes, font=footer_font) + 40
    _draw_bubble_icon(draw, fx2, footer_y - 2, icon_size, FOOTER_COLOR[:3])
    draw.text((fx2 + icon_size + 14, footer_y - 2), comments, font=footer_font, fill=FOOTER_COLOR)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path
