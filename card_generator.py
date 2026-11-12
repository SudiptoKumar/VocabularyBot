from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont

from config import ASSET_DIR, CARD_DIR, SETTINGS
from dataset import WordEntry
from content_ai import EnrichedWord
from font_manager import ensure_fonts

logger = logging.getLogger("vocabulary.card")

# Specimen-label card (Karn's network-wide design, 2026-11-12, first
# shipped in DailyFacts): 1200 wide, never shorter than square, growing
# taller with content — 1200 (1:1), 1500 (4:5), 1800 (2:3). Cream paper,
# ink structure, one red accent, DM Serif Display word type with Archivo
# label type.
W, H = 1200, 1200
CARD_HEIGHTS = (1200, 1500, 1800)
PAPER = (247, 242, 231)
INK = (24, 22, 19)
TEXT_INK = (28, 25, 21)
MUTED = (110, 100, 86)
RED = (224, 26, 44)


def _font(fonts: dict[str, Path], key: str, size: int, fallback: str) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = fonts.get(key)
    if path and path.exists():
        try:
            return ImageFont.truetype(str(path), size=size)
        except Exception:
            pass
    try:
        return ImageFont.truetype(fallback, size=size)
    except Exception:
        return ImageFont.load_default()


def _logo_mark(height: int = 60) -> Image.Image:
    """The V-book logo with its white margins cropped away and near-white
    pixels made transparent, resized to the badge height."""
    src = ASSET_DIR / "vocabulary_logo.png"
    img = Image.open(src).convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    diff = ImageChops.difference(img, bg).convert("L")
    bbox = diff.point(lambda p: 255 if p > 18 else 0).getbbox()
    if bbox:
        img = img.crop(bbox)
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            if r > 245 and g > 245 and b > 245:
                px[x, y] = (255, 255, 255, 0)
    img = img.resize((round(img.width * height / img.height), height), Image.Resampling.LANCZOS)
    return img


def _tracked_width(draw: ImageDraw.ImageDraw, s: str, font, tracking: int) -> float:
    return sum(draw.textlength(ch, font=font) + tracking for ch in s) - (tracking if s else 0)


def _draw_tracked(draw: ImageDraw.ImageDraw, x: float, y: float, s: str, font, fill, tracking: int) -> float:
    for ch in s:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x


def _tracked_strip(text: str, font, fill, tracking: int) -> Image.Image:
    """Render tracked text on its own strip, cropped to the real pixel
    box, so callers can align it by measured height (e.g. centered on a
    badge's midline) instead of guessing from font metrics."""
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    w = int(_tracked_width(probe, text, font, tracking)) + 8
    strip = Image.new("RGBA", (w, font.size * 2), (0, 0, 0, 0))
    _draw_tracked(ImageDraw.Draw(strip), 4, font.size // 2, text, font, fill, tracking)
    bbox = strip.getbbox()
    return strip.crop(bbox) if bbox else strip


def _dashed_rectangle(draw: ImageDraw.ImageDraw, box, fill, width: int = 3, dash: int = 18, gap: int = 12) -> None:
    x0, y0, x1, y1 = box
    x = x0
    while x < x1:
        nxt = min(x + dash, x1)
        draw.line((x, y0, nxt, y0), fill=fill, width=width)
        draw.line((x, y1, nxt, y1), fill=fill, width=width)
        x += dash + gap
    y = y0
    while y < y1:
        nxt = min(y + dash, y1)
        draw.line((x0, y, x0, nxt), fill=fill, width=width)
        draw.line((x1, y, x1, nxt), fill=fill, width=width)
        y += dash + gap


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int | None = None) -> list[str]:
    words = text.replace("\n", " ").split()
    lines: list[str] = []
    current = ""
    for token in words:
        candidate = (current + " " + token).strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = token
    if current:
        lines.append(current)
    return lines[:max_lines] if max_lines else lines


def _fit_hero(draw: ImageDraw.ImageDraw, text: str, fonts: dict[str, Path], max_width: int, max_height: int):
    """Largest DM Serif size whose wrapped word fits the hero zone."""
    for size in range(118, 39, -2):
        f = _font(fonts, "display", size, "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf")
        lines = _wrap(draw, text, f, max_width)
        if len(lines) * size * 1.15 <= max_height:
            return f, lines, size * 1.15
    f = _font(fonts, "display", 40, "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf")
    return f, _wrap(draw, text, f, max_width), 40 * 1.15


def generate_card(word: WordEntry, enriched: EnrichedWord, position: int, total: int) -> Path:
    """Generate the specimen-label identity card. Pronunciation, the full
    definition, the example and synonyms stay in the rich Telegram post —
    the card carries the word, its type, and its meanings, as before."""
    del position, total  # Pagination markers were intentionally removed from the card.
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    fonts = ensure_fonts()
    safe = word.id.replace("/", "_")
    output = CARD_DIR / f"{safe}.png"

    term = word.term[:1].upper() + word.term[1:]
    pos = enriched.part_of_speech or ("Verb" if word.verb_hint else "Word")
    meta = pos.title()
    if enriched.cefr:
        meta += f"  ·  {enriched.cefr}"

    # Bangla meaning is the primary bilingual cue on the visual card.
    import re
    raw_bn = str(word.meaning_bn_photo or word.meaning_bn or "").strip()
    parts = re.split(r"\s*(?:\||।|,|•)\s*", raw_bn)
    parts = [part.strip(" ,|।") for part in parts if part.strip(" ,|।")]
    bangla_text = "  •  ".join(parts)

    short = (enriched.short_meaning_en.strip() or enriched.definition_en.strip()).replace("\n", " ")

    # Measure pass: pick every font and line break first so the card
    # height can be fitted to the content before anything is drawn.
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    text_w = W - 92 - 92
    hero_font, hero_lines, hero_lh = _fit_hero(probe, term, fonts, text_w, 250)
    bangla = _font(fonts, "hind", 40, "/usr/share/fonts/truetype/noto/NotoSansBengali-Regular.ttf")
    bn_lines = _wrap(probe, bangla_text, bangla, text_w, max_lines=2) if bangla_text else []
    short_font = _font(fonts, "archivo_medium", 30, "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf")
    short_lines = _wrap(probe, short, short_font, text_w, max_lines=2) if short else []

    content_top = 292
    content_h = len(hero_lines) * hero_lh
    if bn_lines:
        content_h += 46 + len(bn_lines) * 56
    if short_lines:
        content_h += 30 + len(short_lines) * 40
    needed = content_top + content_h + 214 + 24
    height = next((h for h in CARD_HEIGHTS if h >= needed), CARD_HEIGHTS[-1])

    image = Image.new("RGB", (W, height), PAPER)
    draw = ImageDraw.Draw(image)

    # Dashed label border.
    _dashed_rectangle(draw, (44, 44, W - 44, height - 44), INK)

    # Header: the channel logo in a white badge; the channel name
    # centered on the badge's midline by its measured pixel box.
    badge_x, badge_y, logo_h = 84, 72, 60
    try:
        logo = _logo_mark(logo_h)
    except Exception:
        logo = None
    badge_w = (logo.width if logo is not None else 97) + 28
    badge_h = logo_h + 28
    badge = Image.new("RGBA", (badge_w, badge_h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge)
    bd.rounded_rectangle((1, 1, badge_w - 2, badge_h - 2), radius=20, fill=(255, 255, 255), outline=INK, width=3)
    if logo is not None:
        badge.alpha_composite(logo, (14, 14))
    else:
        vf = _font(fonts, "archivo_black", 34, "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf")
        vb = bd.textbbox((0, 0), "V", font=vf)
        bd.text(((badge_w - (vb[2] - vb[0])) / 2 - vb[0], (badge_h - (vb[3] - vb[1])) / 2 - vb[1]), "V", font=vf, fill=RED)
    base = image.convert("RGBA")
    base.alpha_composite(badge, (badge_x, badge_y))
    name_font = _font(fonts, "archivo_black", 46, "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf")
    name = _tracked_strip(SETTINGS.channel_name.upper(), name_font, INK, 9)
    base.alpha_composite(name, (badge_x + badge_w + 42, badge_y + badge_h // 2 - name.height // 2))
    image = base.convert("RGB")
    draw = ImageDraw.Draw(image)

    # Header rule, then the word type alone in red.
    draw.line((92, 200, W - 92, 200), fill=INK, width=3)
    kicker_font = _font(fonts, "archivo", 32, "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf")
    _draw_tracked(draw, 92, 238, meta, kicker_font, RED, 5)

    # The word itself in DM Serif Display.
    y = content_top
    for line in hero_lines:
        draw.text((92, y), line, font=hero_font, fill=TEXT_INK)
        y += hero_lh

    # Bangla meaning (Bengali shaping, as before).
    if bn_lines:
        y += 46
        for line in bn_lines:
            draw.text((92, y), line, font=bangla, fill=TEXT_INK, language="bn")
            y += 56

    # Short English meaning in clean smaller type.
    if short_lines:
        y += 30
        for line in short_lines:
            draw.text((92, y), line, font=short_font, fill=MUTED)
            y += 40

    # Footer: ink rule, channel name right on the baseline. The channel
    # is private, so the footer carries the display name, not a handle.
    rule_y = height - 214
    draw.line((92, rule_y, W - 92, rule_y), fill=INK, width=3)
    baseline = rule_y + 74
    hf = _font(fonts, "archivo_black", 38, "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf")
    footer_name = SETTINGS.channel_url.removeprefix("https://t.me/") if SETTINGS.channel_url else SETTINGS.channel_name
    if SETTINGS.channel_url and not footer_name.startswith("@"):
        footer_name = "@" + footer_name
    draw.text((W - 92 - draw.textlength(footer_name, font=hf), baseline - hf.getmetrics()[0]), footer_name, font=hf, fill=INK)

    image.save(output, format="PNG", optimize=True)
    return output
