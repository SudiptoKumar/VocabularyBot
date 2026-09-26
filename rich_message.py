from __future__ import annotations

from typing import Any
import re

from content_ai import EnrichedWord
from dataset import WordEntry


def bold(text: str) -> dict[str, Any]:
    return {"type": "bold", "text": text}


def italic(text: str) -> dict[str, Any]:
    return {"type": "italic", "text": text}


def paragraph(parts: list[Any] | str) -> dict[str, Any]:
    return {"type": "paragraph", "text": parts}


def heading(text: str, size: int = 3) -> dict[str, Any]:
    return {"type": "heading", "text": text, "size": size}


def divider() -> dict[str, Any]:
    return {"type": "divider"}


def pullquote(text: str) -> dict[str, Any]:
    # Telegram Bot API Rich Messages supports native pull quotations. This
    # intentionally replaces the former expandable "Memory Hook" section.
    return {"type": "pullquote", "text": italic(text)}


def table(rows: list[list[Any]], caption: str | None = None, compact: bool = True) -> dict[str, Any]:
    cells = []
    for r_idx, row in enumerate(rows):
        out = []
        for c in row:
            if isinstance(c, dict):
                cell = dict(c)
                cell.setdefault("text", "")
            else:
                cell = {"text": str(c)}
            if r_idx == 0:
                cell["is_header"] = True
            cell.setdefault("align", "left")
            cell.setdefault("valign", "middle")
            out.append(cell)
        cells.append(out)
    result: dict[str, Any] = {
        "type": "table",
        "cells": cells,
        "is_bordered": True,
        "is_striped": True,
        "is_compact": compact,
    }
    if caption:
        result["caption"] = bold(caption)
    return result


def list_block(items: list[str]) -> dict[str, Any]:
    return {"type": "list", "items": [{"label": "•", "blocks": [paragraph(x)]} for x in items]}


def _initial_cap(text: str) -> str:
    """Capitalize the first alphabetic character for table display."""
    value = str(text or "").strip()
    if not value:
        return value
    for i, ch in enumerate(value):
        if ch.isalpha():
            return value[:i] + ch.upper() + value[i + 1:]
    return value


def _table_words(items: list[dict]) -> list[list[str]]:
    rows = [["Word", "Meaning"]]
    for item in items[:4]:
        rows.append([_initial_cap(str(item.get("word", ""))), _initial_cap(str(item.get("meaning", "")))])
    return rows


def _word_family_table(items: list[dict]) -> list[list[str]]:
    rows = [["Word", "Type"]]
    for item in items[:5]:
        rows.append([_initial_cap(str(item.get("word", ""))), _initial_cap(str(item.get("type", "")))])
    return rows


def _section_heading(text: str) -> dict[str, Any]:
    # Core prose headings remain bold paragraphs. Table section titles are
    # rendered as table captions by the caller so Telegram can center them.
    return paragraph(bold(text))


def _normalize_post_bangla_meaning(value: str) -> str:
    """Normalize source Bangla meanings for the rich post.

    The master data may contain Bengali danda punctuation and pipe separators.
    In the post, multiple meanings use comma separators and the line ends with
    one Bengali danda.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = re.split(r"\s*(?:\||।|•)\s*", raw)
    cleaned = [p.strip(" ,|।") for p in parts if p.strip(" ,|।")]
    if not cleaned:
        return ""
    result = ", ".join(cleaned)
    return result.rstrip("।") + "।"


def build_rich_message(
    word: WordEntry,
    enriched: EnrichedWord,
    *,
    card_media: str | None = None,
    audio_media: str | None = None,
    card_attach: str | None = None,
    audio_attach: str | None = None,
) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []

    if card_media or card_attach:
        blocks.append({"type": "photo", "photo": {"type": "photo", "media": card_media or f"attach://{card_attach}"}})

    display_word = word.term[:1].upper() + word.term[1:]
    blocks.append(heading(display_word, 1))

    # The card already carries type/level. Keep pronunciation in the post.
    if word.ipa or word.pronunciation_bn:
        pron = []
        if word.ipa:
            pron.append(word.ipa)
        if word.pronunciation_bn:
            pron.append(word.pronunciation_bn)
        blocks.append(paragraph([bold("Pronunciation  "), "  ·  ".join(pron)]))

    # The requested layout puts the definition under a bold standalone label.
    definition = enriched.definition_en or enriched.short_meaning_en
    if definition:
        blocks.append(_section_heading("Meaning"))
        blocks.append(paragraph(definition))

    # Bangla meaning uses the requested "অর্থ⦂" label, with the meaning on the
    # same line. There is no extra বাংলা/translation label.
    if word.meaning_bn:
        bn = _normalize_post_bangla_meaning(word.meaning_bn)
        if bn:
            blocks.append(paragraph([bold("অর্থ⦂ "), bn]))

    if audio_media or audio_attach:
        # Standard RichBlockAudio uses Telegram's streamable audio player instead
        # of a voice-note bubble. This is the closest supported Bot API behavior
        # to a preloaded one-tap pronunciation player. Client-side auto-download
        # remains under the Telegram user's media settings.
        blocks.append({
            "type": "audio",
            "audio": {
                "type": "audio",
                "media": audio_media or f"attach://{audio_attach}",
                "title": word.term[:64],
            },
        })

    # One intentional divider before the example section. Subsequent sections
    # are separated by headings/content, not repeated horizontal rules.
    if enriched.example_en:
        blocks.append(divider())
        blocks.append(_section_heading("Example"))
        blocks.append(paragraph(enriched.example_en))
        if enriched.example_bn:
            blocks.append(paragraph(enriched.example_bn))

    # Table captions are the centered section labels. Table cells keep the
    # normal readable left alignment.
    if enriched.synonyms:
        blocks.append(table(_table_words(enriched.synonyms), caption="Synonyms"))

    if enriched.antonyms:
        blocks.append(table(_table_words(enriched.antonyms), caption="Antonyms"))

    if enriched.word_family:
        blocks.append(table(_word_family_table(enriched.word_family), caption="Word Family"))

    # Common Collocations, Common Mistake, and Quiz were intentionally removed
    # from the final post format.

    # Memory Hook is intentionally a native pullquote in the middle of the
    # lesson, with no separate "Memory Hook" section heading.
    if enriched.memory_hook:
        blocks.append(pullquote(enriched.memory_hook))

    return {"blocks": blocks, "is_rtl": False}
