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

CARD_COLOR = (255, 255, 255, 255)
TITLE_COLOR = (10, 10, 10, 255)
META_COLOR = (120, 124, 126, 255)
FOOTER_COLOR = (120, 124, 126, 255)
AVATAR_PALETTE = [
    (255, 69, 0), (0, 121, 211), (70, 209, 128),
    (255, 140, 0), (149, 90, 232), (232, 90, 150),
]
# Award badges next to the username. These are NOT Reddit's own award
# art (that's Reddit's proprietary/trademarked IP and can't be lifted
# into a monetized video pipeline) -- they're openly-licensed Twemoji
# graphics (CC-BY 4.0, https://github.com/twitter/twemoji) that read as
# "award coins" the same way Reddit's do. Cached to ASSET_BADGE_DIR on
# first use; ship them there yourself to avoid any network dependency
# at render time.
AWARD_EMOJI_CODEPOINTS = ["1f3c6", "1f48e", "1f3c5"]  # trophy, gem, sports medal
ASSET_BADGE_DIR = Path("assets/badges")
BADGE_CACHE_DIR = Path("build/badges")
TWEMOJI_URL = "https://raw.githubusercontent.com/twitter/twemoji/master/assets/72x72/{code}.png"

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


def _load_badge_image(code: str, size: int) -> Image.Image | None:
    """Loads one award badge as an RGBA image, checking (in order): a
    locally bundled copy under ASSET_BADGE_DIR, a previously-downloaded
    copy under BADGE_CACHE_DIR, then falls back to downloading it from
    Twemoji (CC-BY 4.0). Returns None if none of that works, so callers
    can skip the badge instead of crashing the whole render."""
    local = ASSET_BADGE_DIR / f"{code}.png"
    cached = BADGE_CACHE_DIR / f"{code}.png"

    for candidate in (local, cached):
        if candidate.exists():
            return Image.open(candidate).convert("RGBA").resize((size, size), Image.LANCZOS)

    try:
        import requests

        r = requests.get(TWEMOJI_URL.format(code=code), timeout=10)
        r.raise_for_status()
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(r.content)
        return Image.open(cached).convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception as e:
        print(f"RedditCard: couldn't load award badge {code} ({e}); skipping it.")
        return None


def _draw_awards_row(img: Image.Image, x: int, cy: int, size: int = 26) -> int:
    """Pastes a small row of award badge images starting at x, vertically
    centered on cy. Returns the x position right after the last badge."""
    step = int(size * 0.8)
    placed = 0
    for i, code in enumerate(AWARD_EMOJI_CODEPOINTS):
        badge = _load_badge_image(code, size)
        if badge is None:
            continue
        bx = x + i * step
        by = int(cy - size / 2)
        img.alpha_composite(badge, (bx, by))
        placed = i + 1
    return x + size + max(0, placed - 1) * step + 8


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
    username = username or random_username(rng)
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
    _draw_awards_row(img, int(awards_x), int(name_y + username_font.size / 2))

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
