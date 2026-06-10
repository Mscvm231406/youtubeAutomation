"""Render authentic Reddit-style cards (post + comments) as transparent PNGs.

Uses Pillow + Windows Segoe UI fonts (the same family Reddit's UI uses). Each
card is sized to its content and returned as an RGBA image, ready to overlay
centered on the video while that segment is narrated.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .utils import ROOT

# --- palette (Reddit dark mode) ---
CARD_BG = (26, 26, 27, 255)        # #1A1A1B
TEXT = (215, 218, 220, 255)        # near-white
WHITE = (255, 255, 255, 255)
MUTED = (129, 131, 132, 255)       # #818384 gray
ORANGE = (255, 69, 0, 255)         # Reddit #FF4500
UPVOTE = (255, 69, 0, 255)

FONT_DIR = Path("C:/Windows/Fonts")
CARD_W = 960
PAD = 48
RADIUS = 28


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default()


def _fonts():
    return {
        "bold": lambda s: _font("segoeuib.ttf", s),
        "semi": lambda s: _font("seguisb.ttf", s),
        "reg": lambda s: _font("segoeui.ttf", s),
    }


def format_score(n: int) -> str:
    n = int(n)
    if abs(n) >= 1000:
        return f"{n/1000:.1f}k".replace(".0k", "k")
    return str(n)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _measure_block(draw, text, font, max_w, line_h) -> tuple[list[str], int]:
    lines = _wrap(draw, text, font, max_w)
    return lines, len(lines) * line_h


def _draw_avatar(draw: ImageDraw.ImageDraw, x: int, y: int, d: int) -> None:
    """Simple Reddit-orange roundel as the subreddit/user avatar."""
    draw.ellipse([x, y, x + d, y + d], fill=ORANGE)
    # tiny white head to hint at Snoo
    hr = d * 0.30
    cx, cy = x + d / 2, y + d * 0.52
    draw.ellipse([cx - hr, cy - hr, cx + hr, cy + hr], fill=WHITE)


def _draw_upvote(draw, x, y, size, color) -> int:
    """Filled up-triangle; returns width consumed."""
    draw.polygon([(x + size / 2, y), (x, y + size), (x + size, y + size)], fill=color)
    return size


def _render(blocks_fn) -> Image.Image:
    """Two-pass render: measure on a scratch image, then draw to a sized canvas."""
    fonts = _fonts()
    scratch = Image.new("RGBA", (CARD_W, 4000), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(scratch)
    height = blocks_fn(sdraw, fonts, draw=False)

    img = Image.new("RGBA", (CARD_W, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, CARD_W - 1, height - 1], radius=RADIUS, fill=CARD_BG)
    blocks_fn(draw, fonts, draw=True)
    return img


def render_post_card(subreddit: str, username: str, title: str,
                     score: int, num_comments: int) -> Image.Image:
    inner_w = CARD_W - 2 * PAD

    def builder(dr: ImageDraw.ImageDraw, fonts, draw: bool) -> int:
        y = PAD
        av = 64
        if draw:
            _draw_avatar(dr, PAD, y, av)
            dr.text((PAD + av + 16, y + 2), f"r/{subreddit}", font=fonts["bold"](32), fill=WHITE)
            dr.text((PAD + av + 16, y + 38), f"Posted by u/{username}",
                    font=fonts["reg"](24), fill=MUTED)
        y += av + 20

        tfont = fonts["bold"](46)
        lines, h = _measure_block(dr, title, tfont, inner_w, 58)
        if draw:
            yy = y
            for ln in lines:
                dr.text((PAD, yy), ln, font=tfont, fill=WHITE)
                yy += 58
        y += h + 28

        # footer: upvote + score + comments
        ffont = fonts["semi"](30)
        if draw:
            fx = PAD
            _draw_upvote(dr, fx, y + 4, 26, UPVOTE)
            fx += 26 + 12
            stxt = format_score(score)
            dr.text((fx, y), stxt, font=ffont, fill=TEXT)
            fx += int(dr.textlength(stxt, font=ffont)) + 40
            # comment bubble
            dr.rounded_rectangle([fx, y + 4, fx + 26, y + 24], radius=6, outline=MUTED, width=3)
            fx += 26 + 12
            dr.text((fx, y), f"{format_score(num_comments)}", font=ffont, fill=MUTED)
        y += 36
        return y + PAD

    return _render(lambda dr, fonts, draw: builder(dr, fonts, draw))


def render_comment_card(username: str, body: str, score: int) -> Image.Image:
    inner_w = CARD_W - 2 * PAD

    def builder(dr: ImageDraw.ImageDraw, fonts, draw: bool) -> int:
        y = PAD
        av = 52
        if draw:
            _draw_avatar(dr, PAD, y, av)
            dr.text((PAD + av + 14, y + 2), f"u/{username}",
                    font=fonts["semi"](28), fill=WHITE)
            tx = PAD + av + 14
            ty = y + 34
            _draw_upvote(dr, tx, ty + 2, 18, UPVOTE)
            dr.text((tx + 26, ty), f"{format_score(score)}",
                    font=fonts["reg"](22), fill=MUTED)
        y += av + 18

        bfont = fonts["reg"](38)
        lines, h = _measure_block(dr, body, bfont, inner_w, 50)
        if draw:
            yy = y
            for ln in lines:
                dr.text((PAD, yy), ln, font=bfont, fill=TEXT)
                yy += 50
        y += h + 8
        return y + PAD

    return _render(lambda dr, fonts, draw: builder(dr, fonts, draw))


def save_card(img: Image.Image, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path
