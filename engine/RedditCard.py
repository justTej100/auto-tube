"""Renders a "Reddit post" card (avatar, username, title, upvote/comment
counts) as a PNG, then turns it into a short static-hold intro clip that
gets concatenated in front of the narrated segments.

Used by Auto: the script's title becomes the post text, a random handle
stands in for the username, and the stats are randomized within a
realistic band -- this is a stylistic hook card, not a claim that a real
post said this, so nothing here should be presented as an authentic
screenshot of a specific real post/user.
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Match Assemble.py's vertical Shorts/TikTok frame.
CARD_WIDTH = 1080
CARD_HEIGHT = 1920

BG_COLOR = (26, 26, 27)          # Reddit-dark app background behind the card
CARD_COLOR = (255, 255, 255)
TITLE_COLOR = (10, 10, 10)
META_COLOR = (120, 124, 126)
FOOTER_COLOR = (120, 124, 126)
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


def _rounded_card_bounds(draw: ImageDraw.ImageDraw, top: int, height: int) -> tuple[int, int, int, int]:
    margin = 56
    return margin, top, CARD_WIDTH - margin, top + height


def _draw_avatar(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, letter: str, color: tuple) -> None:
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    font = _font(FONT_BOLD, int(r * 1.1))
    bbox = draw.textbbox((0, 0), letter, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cx - w / 2 - bbox[0], cy - h / 2 - bbox[1]), letter, font=font, fill=(255, 255, 255))


def _draw_upvote_icon(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: tuple) -> None:
    draw.polygon(
        [(x + size / 2, y), (x + size, y + size), (x, y + size)],
        fill=color,
    )


def _draw_bubble_icon(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: tuple) -> None:
    draw.rounded_rectangle((x, y, x + size, y + size * 0.75), radius=size * 0.25, outline=color, width=6)
    draw.polygon(
        [
            (x + size * 0.25, y + size * 0.75),
            (x + size * 0.15, y + size * 1.0),
            (x + size * 0.45, y + size * 0.75),
        ],
        fill=color,
    )


def render_card_png(
    title: str,
    out_path: Path,
    username: str | None = None,
    subreddit: str | None = None,
    upvotes: str | None = None,
    comments: str | None = None,
    seed: int | None = None,
) -> Path:
    """Renders a single Reddit-post-card PNG at 1080x1920 and writes it to
    out_path. Any field left None is randomly generated."""
    rng = random.Random(seed)
    username = username or random_username(rng)
    if upvotes is None or comments is None:
        upvotes, comments = random_stats(rng)

    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    title_font = _font(FONT_BOLD, 52)
    meta_font = _font(FONT_REGULAR, 34)
    footer_font = _font(FONT_BOLD, 36)

    padding = 48
    text_width_px = CARD_WIDTH - 56 * 2 - padding * 2
    wrapped = _wrap_to_pixel_width(draw, title, title_font, text_width_px)

    line_height = int(title_font.size * 1.28)
    header_h = 120
    footer_h = 100
    body_h = line_height * len(wrapped) + 24
    card_h = header_h + body_h + footer_h + padding * 2

    top = (CARD_HEIGHT - card_h) // 2
    left, top, right, bottom = _rounded_card_bounds(draw, top, card_h)
    draw.rounded_rectangle((left, top, right, bottom), radius=28, fill=CARD_COLOR)

    # Header: avatar + username (+ subreddit line if given)
    avatar_r = 34
    cx = left + padding + avatar_r
    cy = top + padding + avatar_r
    color = rng.choice(AVATAR_PALETTE)
    _draw_avatar(draw, cx, cy, avatar_r, username[2:3].upper(), color)

    text_x = cx + avatar_r + 20
    if subreddit:
        draw.text((text_x, cy - 32), subreddit, font=_font(FONT_BOLD, 32), fill=TITLE_COLOR)
        draw.text((text_x, cy + 4), username, font=meta_font, fill=META_COLOR)
    else:
        draw.text((text_x, cy - meta_font.size / 2 - 8), username, font=_font(FONT_BOLD, 34), fill=TITLE_COLOR)

    # Body: wrapped title text
    body_top = top + padding + header_h
    y = body_top
    for line in wrapped:
        draw.text((left + padding, y), line, font=title_font, fill=TITLE_COLOR)
        y += line_height

    # Footer: upvote + comment counts
    footer_y = bottom - padding - 44
    icon_size = 34
    fx = left + padding
    _draw_upvote_icon(draw, fx, footer_y, icon_size, (255, 69, 0))
    draw.text((fx + icon_size + 14, footer_y - 4), upvotes, font=footer_font, fill=FOOTER_COLOR)

    fx2 = fx + icon_size + 14 + draw.textlength(upvotes, font=footer_font) + 56
    _draw_bubble_icon(draw, fx2, footer_y - 4, icon_size, FOOTER_COLOR)
    draw.text((fx2 + icon_size + 20, footer_y - 4), comments, font=footer_font, fill=FOOTER_COLOR)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def render_card_clip(
    title: str,
    out_path: Path,
    workdir: Path,
    duration: float = 2.6,
    username: str | None = None,
    subreddit: str | None = None,
    upvotes: str | None = None,
    comments: str | None = None,
    seed: int | None = None,
) -> Path:
    """Renders the card PNG and holds it as a short silent mp4 clip (with a
    gentle zoom-in) at the pipeline's standard vertical resolution/codec so
    it can be concatenated with the narrated segment clips."""
    png_path = workdir / "reddit_card.png"
    render_card_png(
        title,
        png_path,
        username=username,
        subreddit=subreddit,
        upvotes=upvotes,
        comments=comments,
        seed=seed,
    )

    frames = int(round(duration * 30))
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(png_path),
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t", str(duration),
            "-vf",
            f"scale={CARD_WIDTH}:{CARD_HEIGHT},"
            f"zoompan=z='min(zoom+0.0006,1.06)':d={frames}:s={CARD_WIDTH}x{CARD_HEIGHT}:fps=30,"
            f"setsar=1",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            "-shortest",
            str(out_path),
        ],
        check=True,
    )
    return out_path
