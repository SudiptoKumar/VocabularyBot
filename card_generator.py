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

# Compact editorial card: same width as the original, roughly half the height.
W, H = 1080, 675
BG = "#FCFBF8"
INK = "#171717"
MUTED = "#6C6863"
RED = "#E31B2E"
LIGHT = "#EEEAE4"


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


def _crop_logo(size: int = 72) -> Image.Image:
    src = ASSET_DIR / "vocabulary_logo.png"
    img = Image.open(src).convert("RGBA")
    # Crop large white margins from the supplied mark and make near-white pixels transparent.
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
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - img.width) // 2
    y = (size - img.height) // 2
    canvas.alpha_composite(img, (x, y))
    return canvas


def _fit_text(draw: ImageDraw.ImageDraw, text: str, fonts: dict[str, Path], key: str, max_width: int, start: int, minimum: int, fallback_font: str) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = start
    while size >= minimum:
        f = _font(fonts, key, size, fallback_font)
        box = draw.textbbox((0, 0), text, font=f)
        if box[2] - box[0] <= max_width:
            return f
        size -= 2
    return _font(fonts, key, minimum, fallback_font)


def _wrap_by_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int, max_lines: int = 2) -> list[str]:
    words = text.replace("\n", " ").split()
    lines: list[str] = []
    current = ""
    for token in words:
        candidate = (current + " " + token).strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = token
            if len(lines) >= max_lines - 1:
                break
    if current:
        lines.append(current)
    return lines[:max_lines]


def _wrap_bn(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int, max_lines: int = 2) -> list[str]:
    words = text.replace("\n", " ").split()
    lines: list[str] = []
    current = ""
    for token in words:
        candidate = (current + " " + token).strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = token
            if len(lines) >= max_lines - 1:
                break
    if current:
        lines.append(current)
    return lines[:max_lines]


def generate_card(word: WordEntry, enriched: EnrichedWord, position: int, total: int) -> Path:
    """Generate the compact visual identity card. Details stay in the rich Telegram post."""
    del position, total  # Pagination markers were intentionally removed from the card.
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    fonts = ensure_fonts()
    safe = word.id.replace("/", "_")
    output = CARD_DIR / f"{safe}.png"

    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((24, 24, W - 24, H - 24), radius=24, outline=LIGHT, width=2)
    draw.rounded_rectangle((48, 136, 60, 520), radius=6, fill=RED)

    logo = _crop_logo(70)
    image.paste(logo, ((W - logo.width) // 2, 32), logo)

    ui_small = _font(fonts, "jakarta", 19, "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf")
    ui = _font(fonts, "jakarta", 24, "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf")
    ui_bold = _font(fonts, "jakarta", 25, "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf")
    hero = _fit_text(draw, word.term[:1].upper() + word.term[1:], fonts, "playfair", 850, 92, 52, "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf")
    bangla = _font(fonts, "hind", 31, "/usr/share/fonts/truetype/noto/NotoSansBengali-Regular.ttf")
    draw.text((W // 2, 108), "DAILY VOCABULARY", anchor="mm", fill=MUTED, font=ui_small)
    draw.text((W // 2, 205), word.term[:1].upper() + word.term[1:], anchor="mm", fill=INK, font=hero)

    # The photo card intentionally omits IPA/pronunciation. Pronunciation remains in the rich post.
    pos = enriched.part_of_speech or ("Verb" if word.verb_hint else "Word")
    meta = pos.title()
    if enriched.cefr:
        meta += f"  ·  {enriched.cefr}"
    draw.text((W // 2, 305), meta, anchor="mm", fill=INK, font=ui_bold)

    # Small editorial divider after Type. No extra section label and no pagination counter.
    divider_y = 345
    draw.line((330, divider_y, 750, divider_y), fill=LIGHT, width=2)
    draw.ellipse((W // 2 - 5, divider_y - 5, W // 2 + 5, divider_y + 5), fill=RED)

    # Bangla meaning is the primary bilingual cue on the visual card.
    # Use Bengali shaping explicitly and prefer Hind Siliguri, with Noto Sans Bengali as a safe fallback.
    bangla_text = word.meaning_bn.replace(" | ", "  •  ").strip("।")
    bn_lines = _wrap_bn(draw, bangla_text, bangla, 800, max_lines=2)
    start_y = 410 - (len(bn_lines) - 1) * 18
    for line in bn_lines:
        draw.text((W // 2, start_y), line, anchor="mm", fill=INK, font=bangla, language="bn")
        start_y += 48

    short = (enriched.short_meaning_en.strip() or enriched.definition_en.strip()).replace("\n", " ")
    if short:
        short_font = _font(fonts, "jakarta", 21, "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf")
        short_lines = _wrap_by_width(draw, short, short_font, 780, max_lines=2)
        y2 = 500 - (len(short_lines) - 1) * 11
        for line in short_lines:
            draw.text((W // 2, y2), line, anchor="mm", fill=MUTED, font=short_font)
            y2 += 34

    # Keep only the simple text cue, not a button.
    draw.text((W // 2, 592), "LISTEN", anchor="mm", fill=RED, font=ui_bold)
    draw.text((W - 58, 624), SETTINGS.channel_name.upper(), anchor="ra", fill=MUTED, font=ui_small)

    image.save(output, format="PNG", optimize=True)
    return output
