from __future__ import annotations

from typing import Any

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


def table(rows: list[list[Any]], caption: str, compact: bool = True) -> dict[str, Any]:
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
    return {
        "type": "table",
        "cells": cells,
        "is_bordered": True,
        "is_striped": True,
        "is_compact": compact,
        "caption": bold(caption),
    }


def details(summary: str, blocks: list[dict], open_default: bool = False) -> dict[str, Any]:
    return {"type": "details", "summary": bold(summary), "blocks": blocks, "is_open": open_default}


def list_block(items: list[str]) -> dict[str, Any]:
    return {"type": "list", "items": [{"label": "•", "blocks": [paragraph(x)]} for x in items]}


def _table_words(items: list[dict]) -> list[list[str]]:
    rows = [["Word", "Meaning"]]
    for item in items[:4]:
        rows.append([str(item.get("word", "")), str(item.get("meaning", ""))])
    return rows


def _word_family_table(items: list[dict]) -> list[list[str]]:
    rows = [["Word", "Type"]]
    for item in items[:5]:
        rows.append([str(item.get("word", "")), str(item.get("type", ""))])
    return rows


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

    definition = enriched.definition_en or enriched.short_meaning_en
    if definition:
        blocks.append(paragraph([bold("Meaning  "), definition]))

    if word.meaning_bn:
        blocks.append(paragraph([bold("বাংলা অর্থ  "), word.meaning_bn]))

    if audio_media or audio_attach:
        blocks.append({
            "type": "voice_note",
            "voice_note": {"type": "voice_note", "media": audio_media or f"attach://{audio_attach}"},
        })

    if enriched.example_en:
        blocks.append(divider())
        blocks.append(heading("Example", 3))
        blocks.append(paragraph(enriched.example_en))
        if enriched.example_bn:
            # Intentionally no extra "বাংলা" label. The Bangla sentence stands alone.
            blocks.append(paragraph(enriched.example_bn))

    if enriched.synonyms:
        blocks.append(divider())
        blocks.append(table(_table_words(enriched.synonyms), "Synonyms"))

    if enriched.antonyms:
        blocks.append(divider())
        blocks.append(table(_table_words(enriched.antonyms), "Antonyms"))

    if enriched.word_family:
        blocks.append(divider())
        blocks.append(details("Word Family", [table(_word_family_table(enriched.word_family), "Word Family")]))

    if enriched.collocations:
        blocks.append(divider())
        blocks.append(details("Common Collocations", [list_block(enriched.collocations)]))

    if enriched.common_mistake_wrong and enriched.common_mistake_correct:
        blocks.append(divider())
        mistake_blocks = [
            paragraph([bold("❌  "), enriched.common_mistake_wrong]),
            paragraph([bold("✅  "), enriched.common_mistake_correct]),
        ]
        blocks.append(details("Common Mistake", mistake_blocks))

    if enriched.memory_hook:
        blocks.append(divider())
        blocks.append(details("Memory Hook", [paragraph(enriched.memory_hook)], open_default=True))

    return {"blocks": blocks, "is_rtl": False}
