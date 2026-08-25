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

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Overlay is sized well under the 1080-wide frame so the footage behind
# it stays visible around the edges.
CARD_WIDTH = 760

# The name shown on the card. Change this string to whatever you want
# it to always say (e.g. "JazzyStories100"). Leave it as-is and every
# card uses this exact name -- nothing is randomized unless you pass a
# different `username=` into render_card_png() yourself.
DEFAULT_USERNAME = "JazzyStories100"

CARD_COLOR = (255, 255, 255, 255)
TITLE_COLOR = (10, 10, 10, 255)
META_COLOR = (120, 124, 126, 255)
FOOTER_COLOR = (120, 124, 126, 255)
AVATAR_PALETTE = [
    (255, 69, 0), (0, 121, 211), (70, 209, 128),
    (255, 140, 0), (149, 90, 232), (232, 90, 150),
]
# Award badges next to the username. These are original vector shapes
# drawn from scratch below (shield / flame / crown / gift / bell / coin /
# heart, defined further down next to BADGE_DRAWERS) -- not a copy of
# Reddit's own award art, which is Reddit's proprietary/trademarked IP
# and can't be lifted into a monetized video pipeline.

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
FONT_BOLD = FONT_DIR / "DejaVuSans-Bold.ttf"
FONT_REGULAR = FONT_DIR / "DejaVuSans.ttf"

ADJECTIVES = [
    "quiet", "tired", "salty", "honest", "random", "lucky", "grumpy",
    "sleepy", "petty", "stubborn", "lowkey", "average", "nervous",
    "jazzy", "cozy", "moody", "spicy", "dreamy", "wild", "chill",
]
NOUNS = [
    "throwaway", "raccoon", "commuter", "hermit", "gremlin", "houseplant",
    "insomniac", "barista", "goblin", "wanderer", "typo", "pigeon",
    "stories", "diaries", "confessions", "chronicles", "tales",
]


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default()


def random_username(rng: random.Random) -> str:
    """Plain display-name style handle, e.g. 'jazzystories100' -- no
    u/ prefix, not tied to any real subreddit or account."""
    return f"{rng.choice(ADJECTIVES)}{rng.choice(NOUNS)}{rng.randint(10, 999)}"


def format_count(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}K".replace(".0K", "K")
    return str(n)


def random_stats(rng: random.Random) -> tuple[str, str]:
    likes = rng.randint(8_000, 140_000)
    comments = int(likes * rng.uniform(0.15, 0.45))
    return format_count(likes), format_count(comments)


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


def _draw_heart_icon(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: tuple) -> None:
    lobe_d = size * 0.55
    draw.ellipse((x, y, x + lobe_d, y + lobe_d), fill=color + (255,))
    draw.ellipse((x + size - lobe_d, y, x + size, y + lobe_d), fill=color + (255,))
    draw.polygon(
        [
            (x, y + lobe_d * 0.55),
            (x + size, y + lobe_d * 0.55),
            (x + size / 2, y + size),
        ],
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


def _star_points(cx: float, cy: float, r_outer: float, r_inner: float) -> list[tuple[float, float]]:
    points = []
    for i in range(10):
        angle = math.radians(-90 + i * 36)
        r = r_outer if i % 2 == 0 else r_inner
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return points


def _draw_shield_badge(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color: tuple) -> None:
    """Original shield-shaped badge with a star punched into it."""
    pts = [(0, -1), (0.85, -0.6), (0.85, 0.15), (0, 1), (-0.85, 0.15), (-0.85, -0.6)]
    poly = [(cx + px * r, cy + py * r) for px, py in pts]
    draw.polygon(poly, fill=color + (255,))
    ring = tuple(max(0, c - 45) for c in color)
    draw.line(poly + [poly[0]], fill=ring + (255,), width=2)
    draw.polygon(_star_points(cx, cy - r * 0.05, r * 0.42, r * 0.18), fill=(255, 255, 255, 235))


def _draw_flame_badge(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color: tuple) -> None:
    """Original flame/teardrop badge, two-tone (outer + inner lick)."""
    outer = [
        (0, -1), (0.55, -0.5), (0.3, -0.05), (0.55, 0.4),
        (0, 1), (-0.55, 0.4), (-0.3, -0.05), (-0.55, -0.5),
    ]
    poly = [(cx + px * r, cy + py * r) for px, py in outer]
    draw.polygon(poly, fill=color + (255,))
    inner_color = (255, 205, 60)
    inner = [(px * 0.5, py * 0.55 + 0.15) for px, py in outer]
    ipoly = [(cx + px * r, cy + py * r) for px, py in inner]
    draw.polygon(ipoly, fill=inner_color + (255,))


def _draw_crown_badge(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color: tuple) -> None:
    """Original crown badge: zigzag top with three jewel dots, solid base."""
    pts = [
        (-0.8, 0.9), (-0.8, -0.15), (-0.4, 0.35), (0, -0.9),
        (0.4, 0.35), (0.8, -0.15), (0.8, 0.9),
    ]
    poly = [(cx + px * r, cy + py * r) for px, py in pts]
    draw.polygon(poly, fill=color + (255,))
    ring = tuple(max(0, c - 45) for c in color)
    draw.line(poly + [poly[0]], fill=ring + (255,), width=2)
    for jx in (-0.4, 0.0, 0.4):
        jcx, jcy = cx + jx * r, cy + (-0.15 if jx else -0.75) * r
        jr = r * 0.13
        draw.ellipse((jcx - jr, jcy - jr, jcx + jr, jcy + jr), fill=(255, 255, 255, 235))


def _draw_gift_badge(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color: tuple) -> None:
    """Original gift-box badge: box body, cross ribbon, bow on top."""
    box = (cx - 0.8 * r, cy - 0.3 * r, cx + 0.8 * r, cy + 0.9 * r)
    draw.rectangle(box, fill=color + (255,))
    lid = (cx - 0.9 * r, cy - 0.55 * r, cx + 0.9 * r, cy - 0.25 * r)
    draw.rectangle(lid, fill=color + (255,))
    ribbon_color = (255, 255, 255, 235)
    draw.rectangle((cx - 0.12 * r, cy - 0.55 * r, cx + 0.12 * r, cy + 0.9 * r), fill=ribbon_color)
    draw.rectangle((cx - 0.9 * r, cy - 0.45 * r, cx + 0.9 * r, cy - 0.32 * r), fill=ribbon_color)
    bow_r = r * 0.22
    draw.ellipse((cx - bow_r * 2, cy - 0.75 * r - bow_r, cx, cy - 0.75 * r + bow_r), fill=ribbon_color)
    draw.ellipse((cx, cy - 0.75 * r - bow_r, cx + bow_r * 2, cy - 0.75 * r + bow_r), fill=ribbon_color)


def _draw_bell_badge(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color: tuple) -> None:
    """Original bell badge: dome body, base rim, small clapper."""
    draw.pieslice((cx - 0.75 * r, cy - 0.85 * r, cx + 0.75 * r, cy + 0.65 * r), 180, 360, fill=color + (255,))
    draw.rectangle((cx - 0.75 * r, cy - 0.1 * r, cx + 0.75 * r, cy + 0.5 * r), fill=color + (255,))
    draw.rectangle((cx - 0.9 * r, cy + 0.45 * r, cx + 0.9 * r, cy + 0.62 * r), fill=color + (255,))
    clapper_r = r * 0.16
    draw.ellipse(
        (cx - clapper_r, cy + 0.62 * r, cx + clapper_r, cy + 0.62 * r + clapper_r * 2),
        fill=color + (255,),
    )
    top_r = r * 0.1
    draw.ellipse((cx - top_r, cy - 0.98 * r, cx + top_r, cy - 0.98 * r + top_r * 2), fill=color + (255,))


def _draw_coin_badge(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color: tuple) -> None:
    """Original coin badge: filled circle, ring, star mark in the middle."""
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color + (255,))
    ring = tuple(max(0, c - 45) for c in color)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ring + (255,), width=2)
    draw.ellipse((cx - r * 0.72, cy - r * 0.72, cx + r * 0.72, cy + r * 0.72), outline=ring + (255,), width=1)
    draw.polygon(_star_points(cx, cy, r * 0.45, r * 0.2), fill=(255, 255, 255, 235))


def _draw_heart_badge(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color: tuple) -> None:
    """Original heart badge (distinct from the footer like-heart: filled
    solid disc behind it for a coin-backed look)."""
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, 90))
    size = r * 1.3
    x, y = cx - size / 2, cy - size / 2
    _draw_heart_icon(draw, x, y, size, color)


BADGE_DRAWERS = [
    _draw_shield_badge,
    _draw_flame_badge,
    _draw_crown_badge,
    _draw_gift_badge,
    _draw_bell_badge,
    _draw_coin_badge,
    _draw_heart_badge,
]
BADGE_COLORS = [
    (255, 186, 8),    # shield - gold
    (255, 90, 54),    # flame - orange/red
    (149, 90, 232),   # crown - purple
    (255, 105, 180),  # gift - pink
    (255, 196, 0),    # bell - yellow/gold
    (0, 168, 232),    # coin - blue
    (232, 74, 95),    # heart - red
]


def _draw_awards_row(
    draw: ImageDraw.ImageDraw, x: int, cy: int, r: int = 13, rng: random.Random | None = None, count: int = 3
) -> int:
    """Draws a small row of original award badges (a random mix of
    shield / flame / crown / gift / bell / coin / heart) starting at x,
    vertically centered on cy. Returns the x position right after the
    last badge."""
    rng = rng or random.Random()
    indices = rng.sample(range(len(BADGE_DRAWERS)), k=min(count, len(BADGE_DRAWERS)))
    step = r * 1.9
    for i, idx in enumerate(indices):
        bcx = x + r + i * step
        BADGE_DRAWERS[idx](draw, bcx, cy, r, BADGE_COLORS[idx])
    return int(x + r * 2 + (len(indices) - 1) * step) + 8


def render_card_png(
    title: str,
    out_path: Path,
    username: str | None = None,
    likes: str | None = None,
    comments: str | None = None,
    seed: int | None = None,
    max_lines: int = 4,
) -> Path:
    """Renders a small transparent card PNG sized to fit its own content
    (not the full frame) so it can be overlaid on top of playing footage.
    Any field left None is randomly generated. Title is clamped to
    max_lines so the overlay never grows large enough to cover the shot."""
    rng = random.Random(seed)
    username = username or DEFAULT_USERNAME
    if likes is None or comments is None:
        likes, comments = random_stats(rng)

    # Small canvas just to measure text; real canvas sized after we know
    # how many lines the title wraps to.
    probe = Image.new("RGBA", (10, 10))
    probe_draw = ImageDraw.Draw(probe)

    title_font = _font(FONT_BOLD, 34)
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

    # Header: avatar + bold name + award badges (no subreddit/u- line)
    avatar_r = 22
    cx = padding + avatar_r
    cy = padding + avatar_r
    color = rng.choice(AVATAR_PALETTE)
    _draw_avatar(draw, cx, cy, avatar_r, username[:1].upper(), color)

    text_x = cx + avatar_r + 14
    username_font = _font(FONT_BOLD, 26)
    name_y = cy - username_font.size / 2 - 2
    draw.text((text_x, name_y), username, font=username_font, fill=TITLE_COLOR)
    awards_x = text_x + draw.textlength(username, font=username_font) + 14
    _draw_awards_row(draw, int(awards_x), int(name_y + username_font.size / 2), rng=rng)

    # Body: wrapped title text
    y = padding + header_h
    for line in wrapped:
        draw.text((padding, y), line, font=title_font, fill=TITLE_COLOR)
        y += line_height

    # Footer: heart (likes) + comment counts
    footer_y = card_h - padding - 30
    icon_size = 24
    fx = padding
    _draw_heart_icon(draw, fx, footer_y, icon_size, (255, 48, 91))
    draw.text((fx + icon_size + 10, footer_y - 2), likes, font=footer_font, fill=FOOTER_COLOR)

    fx2 = fx + icon_size + 10 + draw.textlength(likes, font=footer_font) + 40
    _draw_bubble_icon(draw, fx2, footer_y - 2, icon_size, FOOTER_COLOR[:3])
    draw.text((fx2 + icon_size + 14, footer_y - 2), comments, font=footer_font, fill=FOOTER_COLOR)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path
