from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont

from config import ASSET_DIR, CARD_DIR, SETTINGS
from dataset import WordEntry
from content_ai import EnrichedWord
from font_manager import ensure_fonts

logger = logging.getLogger("vocabulary.card")

W, H = 1080, 1350
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


def _crop_logo(size: int = 84) -> Image.Image:
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


def generate_card(word: WordEntry, enriched: EnrichedWord, position: int, total: int) -> Path:
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    fonts = ensure_fonts()
    safe = word.id.replace("/", "_")
    output = CARD_DIR / f"{safe}.png"

    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)

    # Soft framing and editorial accent.
    draw.rounded_rectangle((28, 28, W - 28, H - 28), radius=28, outline=LIGHT, width=2)
    draw.rounded_rectangle((70, 220, 84, 1130), radius=7, fill=RED)

    logo = _crop_logo(92)
    image.paste(logo, ((W - logo.width) // 2, 78), logo)

    ui = _font(fonts, "jakarta", 28, "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf")
    ui_small = _font(fonts, "jakarta", 22, "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf")
    ui_bold = _font(fonts, "jakarta", 28, "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf")
    hero = _fit_text(draw, word.term.upper(), fonts, "playfair", 870, 118, 66, "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf")
    bangla = _font(fonts, "hind", 46, "/usr/share/fonts/truetype/noto/NotoSansBengali-Regular.ttf")
    ipa_font = _font(fonts, "jakarta", 30, "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf")

    draw.text((W // 2, 195), "DAILY VOCABULARY", anchor="mm", fill=MUTED, font=ui_small)
    draw.text((W // 2, 345), word.term.upper(), anchor="mm", fill=INK, font=hero)

    if word.ipa:
        draw.text((W // 2, 445), word.ipa, anchor="mm", fill=MUTED, font=ipa_font)

    meta = enriched.part_of_speech or ("Verb" if word.verb_hint else "Word")
    if enriched.cefr:
        meta = f"{meta.title()}  ·  {enriched.cefr}"
    draw.text((W // 2, 505), meta, anchor="mm", fill=INK, font=ui_bold)

    # Bangla meaning from the source database.
    bangla_text = word.meaning_bn.replace(" | ", "  •  ").strip("।")
    max_chars = 38 if len(bangla_text) > 35 else 50
    lines = []
    current = ""
    for token in bangla_text.split():
        candidate = (current + " " + token).strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            lines.append(current)
            current = token
    if current:
        lines.append(current)
    lines = lines[:3]
    y = 635 - (len(lines)-1)*28
    for line in lines:
        draw.text((W // 2, y), line, anchor="mm", fill=INK, font=bangla)
        y += 62

    short = enriched.short_meaning_en.strip() or enriched.definition_en.strip()
    if short:
        short = short.replace("\n", " ")
        words = short.split()
        short_lines = []
        cur = ""
        for token in words:
            cand = (cur + " " + token).strip()
            if len(cand) <= 34:
                cur = cand
            else:
                short_lines.append(cur)
                cur = token
        if cur:
            short_lines.append(cur)
        short_lines = short_lines[:2]
        y2 = 845 - (len(short_lines)-1)*22
        for line in short_lines:
            draw.text((W // 2, y2), line, anchor="mm", fill=MUTED, font=ui_bold)
            y2 += 52

    # Tiny audio cue, the actual playable audio lives in the rich message.
    draw.rounded_rectangle((W//2-132, 965, W//2+132, 1020), radius=27, outline=RED, width=2)
    draw.text((W // 2, 993), "LISTEN", anchor="mm", fill=RED, font=ui_bold)

    draw.text((90, 1245), f"{position:02d} / {total:02d}", fill=MUTED, font=ui_small)
    draw.text((W - 90, 1245), SETTINGS.channel_name.upper(), anchor="ra", fill=MUTED, font=ui_small)

    image.save(output, format="PNG", optimize=True)
    return output
