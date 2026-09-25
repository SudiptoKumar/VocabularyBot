from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from audio import ensure_audio
from card_generator import generate_card
from config import CARD_DIR, GENERATED_DIR, SETTINGS, STATE_FILE
from content_ai import EnrichedWord, enrich_words
from dataset import WordEntry, load_all, validate_dataset
from rich_message import build_rich_message
from shuffle_bag import finish_run, mark_failed, mark_published, recover_stale_reservations, reserve_next_batch
from state_store import load_state, save_state, utc_now
from telegram_client import extract_media_ids, send_poll, send_rich_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("vocabulary")


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def _index(words: list[WordEntry]) -> dict[str, WordEntry]:
    return {w.id: w for w in words}


def _cache_enrichment(state: dict, enriched: dict[str, EnrichedWord]) -> None:
    cache = state.setdefault("content_cache", {})
    for wid, content in enriched.items():
        cache[wid] = content.to_dict()


def _enrichment_from_cache(state: dict, words: list[WordEntry]) -> dict[str, EnrichedWord]:
    result: dict[str, EnrichedWord] = {}
    for word in words:
        raw = (state.get("content_cache") or {}).get(word.id)
        if isinstance(raw, dict):
            result[word.id] = EnrichedWord(
                definition_en=str(raw.get("definition_en", "")),
                short_meaning_en=str(raw.get("short_meaning_en", "")),
                part_of_speech=str(raw.get("part_of_speech", "")),
                cefr=str(raw.get("cefr", "")),
                example_en=str(raw.get("example_en", "")),
                example_bn=str(raw.get("example_bn", "")),
                synonyms=raw.get("synonyms") if isinstance(raw.get("synonyms"), list) else [],
                antonyms=raw.get("antonyms") if isinstance(raw.get("antonyms"), list) else [],
                word_family=raw.get("word_family") if isinstance(raw.get("word_family"), list) else [],
                collocations=raw.get("collocations") if isinstance(raw.get("collocations"), list) else [],
                common_mistake_wrong=str(raw.get("common_mistake_wrong", "")),
                common_mistake_correct=str(raw.get("common_mistake_correct", "")),
                memory_hook=str(raw.get("memory_hook", "")),
                quiz_question=str(raw.get("quiz_question", "")),
                quiz_options=raw.get("quiz_options") if isinstance(raw.get("quiz_options"), list) else [],
                quiz_correct_index=int(raw.get("quiz_correct_index", 0) or 0),
            )
    return result


def _get_content(state: dict, words: list[WordEntry]) -> dict[str, EnrichedWord]:
    cached = _enrichment_from_cache(state, words)
    missing = [w for w in words if w.id not in cached]
    if missing:
        generated = enrich_words(missing)
        cached.update(generated)
        _cache_enrichment(state, generated)
    return cached


def _publish_one(state: dict, run_id: str, word: WordEntry, enriched: EnrichedWord, cycle: int, position: int, total: int, dry_run: bool) -> bool:
    card = None
    audio = None
    media_cache = state.setdefault("media_cache", {})
    cached_media = media_cache.get(word.id) if isinstance(media_cache.get(word.id), dict) else {}
    # Invalidate old media caches whenever the card/audio presentation changes.
    media = cached_media if cached_media.get("version") == SETTINGS.media_version else {}

    try:
        card = generate_card(word, enriched, position, total)
    except Exception as exc:
        logger.exception("Card generation failed for %s: %s", word.id, exc)
        card = None

    try:
        audio = ensure_audio(word.term)
    except Exception as exc:
        logger.exception("Audio generation failed for %s: %s", word.id, exc)
        audio = None

    if dry_run:
        logger.info("DRY RUN | %s | card=%s | audio=%s", word.term, card, audio)
        if enriched.synonyms or enriched.antonyms:
            logger.info("       synonyms=%s antonyms=%s", enriched.synonyms, enriched.antonyms)
        return True

    card_file_id = str(media.get("photo_file_id") or "")
    audio_file_id = str(media.get("voice_file_id") or "")
    attachments = {}
    if not card_file_id and card:
        attach = f"card_{word.id.replace('-', '_')}"
        attachments[attach] = card
        card_ref = f"attach://{attach}"
    else:
        card_ref = card_file_id or None
    if not audio_file_id and audio:
        attach = f"audio_{word.id.replace('-', '_')}"
        attachments[attach] = audio
        audio_ref = f"attach://{attach}"
    else:
        audio_ref = audio_file_id or None

    rich = build_rich_message(
        word,
        enriched,
        card_media=card_ref if card_ref and card_ref.startswith("tg://") else (None if card_ref and card_ref.startswith("attach://") else card_ref),
        audio_media=audio_ref if audio_ref and audio_ref.startswith("tg://") else (None if audio_ref and audio_ref.startswith("attach://") else audio_ref),
        card_attach=card_ref[len("attach://"):] if card_ref and card_ref.startswith("attach://") else None,
        audio_attach=audio_ref[len("attach://"):] if audio_ref and audio_ref.startswith("attach://") else None,
    )

    try:
        result = send_rich_message(rich, attachments)
        message_id = int(result["message_id"])
        ids = extract_media_ids(result)
        media_cache[word.id] = {
            "version": SETTINGS.media_version,
            "photo_file_id": ids.get("photo") or card_file_id,
            "voice_file_id": ids.get("voice") or ids.get("audio") or audio_file_id,
            "cached_at": utc_now(),
        }
        mark_published(state, run_id, word.id, message_id, cycle=cycle)
        save_state(state)
        logger.info("Published %s -> message %s", word.term, message_id)
    except Exception as exc:
        logger.error("Rich message failed for %s: %s", word.term, exc)
        mark_failed(state, run_id, word.id, str(exc))
        save_state(state)
        return False

    if SETTINGS.quiz_enabled and SETTINGS.quiz_per_word and enriched.quiz_options and len(enriched.quiz_options) == 4:
        try:
            poll = send_poll(
                enriched.quiz_question or f"What does {word.term} mean?",
                enriched.quiz_options,
                enriched.quiz_correct_index,
                explanation=(enriched.short_meaning_en or enriched.definition_en),
            )
            state.setdefault("published", {}).setdefault(word.id, {})["quiz_message_id"] = int(poll["message_id"])
            save_state(state)
        except Exception as exc:
            logger.warning("Quiz poll failed for %s: %s", word.term, exc)
    return True


def run(*, count: int | None = None, dry_run: bool = False) -> int:
    words = load_all()
    findings = validate_dataset(words, strict=SETTINGS.strict_dataset)
    if findings:
        raise RuntimeError("Dataset validation failed:\n" + "\n".join(findings[:50]))

    state = load_state(words)
    recovered = recover_stale_reservations(
        state,
        datetime.now(timezone.utc),
        SETTINGS.stale_reservation_minutes,
    )
    if recovered:
        logger.warning("Recovered %d stale vocabulary reservations.", len(recovered))
        if not dry_run:
            save_state(state)

    run_id = _run_id()
    amount = int(count or SETTINGS.words_per_run)
    amount = max(1, min(amount, 20))
    selected = reserve_next_batch(state, [w.id for w in words], amount, run_id)
    if not dry_run:
        save_state(state)

    by_id = _index(words)
    chosen = [by_id[item["id"]] for item in selected]
    content = _get_content(state, chosen)
    if not dry_run:
        save_state(state)

    successful = 0
    for pos, item in enumerate(selected, start=1):
        word = by_id[item["id"]]
        enriched = content[word.id]
        if _publish_one(state, run_id, word, enriched, int(item["cycle"]), pos, len(selected), dry_run):
            successful += 1

    finish_run(state, run_id)
    if not dry_run:
        save_state(state)
    logger.info("Run complete | selected=%d successful=%d failed=%d", len(selected), successful, len(selected) - successful)
    return 0 if successful == len(selected) else 2


def preview(count: int = 5) -> int:
    words = load_all()
    findings = validate_dataset(words, strict=SETTINGS.strict_dataset)
    if findings:
        raise RuntimeError("Dataset validation failed:\n" + "\n".join(findings[:50]))
    state = load_state(words)
    # Copy state so preview never consumes the persistent shuffle queue.
    state = copy.deepcopy(state)
    selected = reserve_next_batch(state, [w.id for w in words], max(1, min(count, 20)), "preview")
    lookup = _index(words)
    for n, item in enumerate(selected, start=1):
        word = lookup[item["id"]]
        print(f"{n}. {word.id} | cycle={item['cycle']} | {word.term} | {word.ipa} | {word.meaning_bn}")
    return 0


def self_test() -> int:
    words = load_all()
    findings = validate_dataset(words, strict=True)
    assert not findings, "\n".join(findings[:20])
    assert len(words) >= 3000, len(words)
    ids = [w.id for w in words]
    assert len(ids) == len(set(ids))
    assert len({w.normalized_term for w in words}) == len(words)

    # Queue properties across a whole cycle plus boundary batching.
    from shuffle_bag import reserve_next_batch
    test_state = load_state(words)
    test_state = copy.deepcopy(test_state)
    test_state["published"] = {}
    test_state["remaining_ids"] = []
    test_state["cycle"] = 1
    all_ids = [w.id for w in words]
    seen: list[str] = []
    run_no = 0
    while len(seen) < len(all_ids):
        run_no += 1
        picked = reserve_next_batch(test_state, all_ids, 5, f"t{run_no}")
        seen.extend(x["id"] for x in picked)
        if run_no > (len(all_ids) // 5) + 3:
            raise AssertionError("shuffle loop did not terminate")
    # First pool must appear exactly once before all words have been seen.
    assert len(seen) >= len(all_ids)
    assert len(set(seen[:len(all_ids)])) == len(all_ids)

    # Card generator test with source-only fallback content.
    from content_ai import fallback_word
    sample = words[0]
    card = generate_card(sample, fallback_word(sample), 1, 5)
    assert card.exists() and card.stat().st_size > 2000

    # Rich message schema smoke test.
    from rich_message import build_rich_message
    msg = build_rich_message(sample, fallback_word(sample), card_media="attach://card_test", audio_media="attach://audio_test", card_attach="card_test", audio_attach="audio_test")
    assert msg.get("blocks") and any(x.get("type") == "photo" for x in msg["blocks"])
    assert any(x.get("type") == "voice_note" for x in msg["blocks"])

    print(f"SELF-TEST PASS | unique vocabulary words: {len(words)}")
    print(f"Sample card: {card}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Vocabulary Telegram Bot")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.preview:
        return preview(args.count or 5)
    return run(count=args.count, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
