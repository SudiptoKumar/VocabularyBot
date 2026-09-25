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
from shuffle_bag import finish_run, mark_failed, mark_published, mark_unknown, recover_stale_reservations, reserve_next_batch
from state_store import fresh_state, load_state, save_state, utc_now
from telegram_client import TelegramAPIError, TelegramUnknownOutcomeError, extract_media_ids, preflight, send_poll, send_rich_message

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
        # Never persist degraded/source-only AI fallbacks as reusable rich content.
        from content_ai import _is_rich_enough
        if not _is_rich_enough(content):
            continue
        payload = content.to_dict()
        payload["_content_version"] = SETTINGS.content_version
        cache[wid] = payload


def _enrichment_from_cache(state: dict, words: list[WordEntry]) -> dict[str, EnrichedWord]:
    result: dict[str, EnrichedWord] = {}
    from content_ai import _is_rich_enough
    for word in words:
        raw = (state.get("content_cache") or {}).get(word.id)
        if not isinstance(raw, dict):
            continue
        if str(raw.get("_content_version", "")) != SETTINGS.content_version:
            continue
        candidate = EnrichedWord(
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
        if _is_rich_enough(candidate):
            result[word.id] = candidate
    return result


def _get_content(state: dict, words: list[WordEntry]) -> dict[str, EnrichedWord]:
    cached = _enrichment_from_cache(state, words)
    missing = [w for w in words if w.id not in cached]
    if missing:
        generated = enrich_words(missing)
        cached.update({wid: content for wid, content in generated.items()})
        _cache_enrichment(state, generated)
    return cached

def _publish_one(state: dict, run_id: str, word: WordEntry, enriched: EnrichedWord, cycle: int, position: int, total: int, dry_run: bool) -> bool:
    card = None
    audio = None
    media_cache = state.setdefault("media_cache", {})
    cached_media = media_cache.get(word.id) if isinstance(media_cache.get(word.id), dict) else {}
    # Invalidate old media caches whenever the card/audio presentation changes.
    media = cached_media if cached_media.get("version") == SETTINGS.media_version else {}

    if not media.get("photo_file_id"):
        try:
            card = generate_card(word, enriched, position, total)
        except Exception as exc:
            logger.exception("Card generation failed for %s: %s", word.id, exc)
            mark_failed(state, run_id, word.id, f"card generation failed: {exc}")
            if not dry_run:
                save_state(state)
            return False
        if card is None or not Path(card).exists() or Path(card).stat().st_size < 2000:
            mark_failed(state, run_id, word.id, "card generation produced no usable image")
            if not dry_run:
                save_state(state)
            return False

    if SETTINGS.audio_enabled and not media.get("voice_file_id"):
        try:
            audio = ensure_audio(word.term)
        except Exception as exc:
            logger.exception("Audio generation failed for %s: %s", word.term, exc)
            mark_failed(state, run_id, word.id, f"audio generation failed: {exc}")
            if not dry_run:
                save_state(state)
            return False
        if audio is None or not Path(audio).exists() or Path(audio).stat().st_size < 1000:
            mark_failed(state, run_id, word.id, "audio generation produced no usable audio")
            if not dry_run:
                save_state(state)
            return False

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
        if "message_id" not in result:
            raise TelegramUnknownOutcomeError("Telegram returned no message_id after sendRichMessage.")
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
    except TelegramUnknownOutcomeError as exc:
        logger.error("Rich message outcome unknown for %s; quarantining to prevent duplicates: %s", word.term, exc)
        mark_unknown(state, run_id, word.id, str(exc))
        save_state(state)
        return False
    except TelegramAPIError as exc:
        logger.error("Rich message rejected by Telegram for %s: %s", word.term, exc)
        # Drop media cache for this word so a stale file_id is regenerated on retry.
        media_cache.pop(word.id, None)
        mark_failed(state, run_id, word.id, str(exc))
        save_state(state)
        return False
    except Exception as exc:
        logger.exception("Unexpected rich-message failure for %s; treating as unknown outcome: %s", word.term, exc)
        mark_unknown(state, run_id, word.id, str(exc))
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

    if not dry_run and SETTINGS.telegram_preflight:
        preflight()

    state = load_state(words)
    recovered = recover_stale_reservations(
        state,
        datetime.now(timezone.utc),
        SETTINGS.stale_reservation_minutes,
    )
    if recovered:
        logger.warning("Quarantined %d stale vocabulary items with unknown Telegram outcomes.", len(recovered))
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

    from content_ai import _is_rich_enough
    incomplete = [word for word in chosen if not _is_rich_enough(content.get(word.id, EnrichedWord()))]
    if incomplete and SETTINGS.fail_closed_on_incomplete_content:
        reason = "AI enrichment incomplete; refusing to publish degraded vocabulary content"
        for word in chosen:
            mark_failed(state, run_id, word.id, reason if word in incomplete else "run aborted because one or more selected words lacked complete enrichment")
        finish_run(state, run_id)
        if not dry_run:
            save_state(state)
        logger.error("Run aborted safely; incomplete enrichment for: %s", ", ".join(w.term for w in incomplete))
        return 2

    successful = 0
    unknown = 0
    for pos, item in enumerate(selected, start=1):
        word = by_id[item["id"]]
        enriched = content[word.id]
        if _publish_one(state, run_id, word, enriched, int(item["cycle"]), pos, len(selected), dry_run):
            successful += 1
        run_record = (state.get("reserved_runs") or {}).get(run_id, {})
        if word.id in set(run_record.get("unknown_ids", [])):
            unknown += 1

    finish_run(state, run_id)
    if not dry_run:
        save_state(state)
    logger.info(
        "Run complete | selected=%d successful=%d failed=%d unknown=%d",
        len(selected), successful, len(selected) - successful - unknown, unknown,
    )
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
    # Self-test must be independent of the real persistent publication ledger.
    # A live state branch may legitimately contain reservations/publications.
    test_state = fresh_state(words)
    all_ids = [w.id for w in words]
    seen: list[str] = []
    run_no = 0
    while len(seen) < len(all_ids):
        run_no += 1
        run_id = f"t{run_no}"
        picked = reserve_next_batch(test_state, all_ids, 5, run_id)
        seen.extend(x["id"] for x in picked)
        for item in picked:
            mark_published(test_state, run_id, item["id"], run_no, cycle=int(item["cycle"]))
        finish_run(test_state, run_id)
        if run_no > (len(all_ids) // 5) + 3:
            raise AssertionError("shuffle loop did not terminate")
    # First pool must appear exactly once before all words have been seen.
    assert len(seen) >= len(all_ids)
    assert len(set(seen[:len(all_ids)])) == len(all_ids)
    assert len(seen) == ((len(all_ids) + 4) // 5) * 5
    # The 3521-word pool intentionally crosses a 5-word batch boundary.
    # The overflow belongs to the next cycle and must not duplicate any word
    # from the completed cycle.
    assert len(set(seen[:len(all_ids)])) == len(all_ids)

    # Regression test for the V1.0.2 bug: lifetime publication history must
    # never empty the queue for the current/new cycle.
    regression = fresh_state(words)
    regression["queue_initialized"] = True
    regression["cycle"] = 1
    regression["remaining_ids"] = all_ids[5:]
    regression["published"] = {wid: {"last_cycle": 1} for wid in all_ids[:5]}
    from shuffle_bag import ensure_queue
    ensure_queue(regression, all_ids)
    assert regression["remaining_ids"] == all_ids[5:]

    # Failure must return a word to the same queue instead of depending on
    # lifetime publication history.
    recovery = fresh_state(words)
    picked = reserve_next_batch(recovery, all_ids, 5, "failure-test")
    failed_id = picked[0]["id"]
    mark_failed(recovery, "failure-test", failed_id, "simulated failure")
    assert recovery["remaining_ids"][0] == failed_id

    # Card generator test with source-only fallback content.
    from content_ai import fallback_word
    sample = words[0]
    card = generate_card(sample, fallback_word(sample), 1, 5)
    from card_generator import W as CARD_W, H as CARD_H
    assert (CARD_W, CARD_H) == (1080, 675)
    assert card.exists() and card.stat().st_size > 2000

    # Rich message schema smoke test, including the exact requested example format.
    from rich_message import build_rich_message
    example_en = "Many friends attended the funeral to pay their respects."
    example_bn = "অনেক বন্ধু পারলৌকিক অনুষ্ঠানে উপস্থিত ছিলেন।"
    rich_sample = fallback_word(sample)
    rich_sample.example_en = example_en
    rich_sample.example_bn = example_bn
    rich_sample.definition_en = "A sample definition."
    rich_sample.synonyms = [{"word": "companion", "meaning": "a friend"}]
    rich_sample.antonyms = [{"word": "stranger", "meaning": "an unfamiliar person"}]
    rich_sample.word_family = [{"word": "companionship", "type": "Noun"}]
    rich_sample.collocations = ["close companion"]
    rich_sample.common_mistake_wrong = "use the wrong word"
    rich_sample.common_mistake_correct = "use the correct word"
    rich_sample.memory_hook = "Remember the word with a simple image."
    msg = build_rich_message(
        sample,
        rich_sample,
        card_media="attach://card_test",
        audio_media="attach://audio_test",
        card_attach="card_test",
        audio_attach="audio_test",
    )
    assert msg.get("blocks") and any(x.get("type") == "photo" for x in msg["blocks"])
    assert any(x.get("type") == "voice_note" for x in msg["blocks"])

    # Exact post-format regression checks for V1:
    # - Meaning is a bold standalone section heading followed by definition.
    # - Bangla meaning uses the requested single-line "অর্থ⦂" label.
    # - Example is English followed directly by Bangla, with no বাংলা label.
    # - Only one intentional divider remains, before Example.
    # - All core section headings are bold paragraph headings, not emoji/table captions.
    # - Memory Hook is a native pullquote with no separate heading/details block.
    paragraph_blocks = [x for x in msg["blocks"] if x.get("type") == "paragraph"]
    divider_blocks = [x for x in msg["blocks"] if x.get("type") == "divider"]
    assert len(divider_blocks) == 1
    assert any(x.get("text") == {"type": "bold", "text": "Meaning"} for x in paragraph_blocks)
    assert any(x.get("text") == {"type": "bold", "text": "Example"} for x in paragraph_blocks)
    assert any(x.get("text") == {"type": "bold", "text": "Synonyms"} for x in paragraph_blocks)
    assert any(x.get("text") == {"type": "bold", "text": "Antonyms"} for x in paragraph_blocks)
    assert any(x.get("text") == {"type": "bold", "text": "Word Family"} for x in paragraph_blocks)
    assert any(x.get("text") == {"type": "bold", "text": "Common Collocations"} for x in paragraph_blocks)
    assert any(x.get("text") == {"type": "bold", "text": "Common Mistake"} for x in paragraph_blocks)
    assert any(x.get("type") == "pullquote" for x in msg["blocks"])
    assert not any(x.get("type") == "details" for x in msg["blocks"])

    example_texts = [
        x.get("text")
        for x in paragraph_blocks
        if isinstance(x.get("text"), str)
    ]
    assert example_en in example_texts
    assert example_bn in example_texts
    assert all("বাংলা  " not in str(text) for text in example_texts)
    bn_blocks = [x.get("text") for x in paragraph_blocks if isinstance(x.get("text"), list)]
    assert any(
        isinstance(text, list)
        and text
        and isinstance(text[0], dict)
        and text[0].get("type") == "bold"
        and text[0].get("text") == "অর্থ⦂ "
        for text in bn_blocks
    )

    # AI recovery regression: a malformed batch response must not collapse the
    # five-word post into source-only fallback content. The production code
    # retries missing words independently; this test exercises that contract
    # without calling the external API.
    import content_ai as _ai
    original_request_json = _ai._request_json
    original_ai_key = SETTINGS.cerebras_api_key
    original_use_ai = SETTINGS.use_cerebras
    try:
        object.__setattr__(SETTINGS, "cerebras_api_key", "self-test-key")
        object.__setattr__(SETTINGS, "use_cerebras", True)

        def _fake_request_json(system, user, schema, *, max_tokens, attempts=2):
            raw_word = user.split('"word":"', 1)[1].split('"', 1)[0]
            if '"properties":{"items"' in json.dumps(schema, ensure_ascii=False, separators=(",", ":")):
                return {}  # Force per-word recovery path.
            options = ["correct meaning", "wrong one", "another wrong", "last wrong"]
            return {
                "item": {
                    "id": words[0].id if raw_word == words[0].term else next(w.id for w in words if w.term == raw_word),
                    "definition_en": f"A clear definition of {raw_word}.",
                    "short_meaning_en": "clear meaning",
                    "part_of_speech": "Noun",
                    "cefr": "B1",
                    "example_en": f"This is an example with {raw_word}.",
                    "example_bn": f"এটি {raw_word}-এর একটি উদাহরণ।",
                    "synonyms": [{"word": "similar", "meaning": "nearly the same"}],
                    "antonyms": [{"word": "opposite", "meaning": "contrary"}],
                    "word_family": [{"word": f"{raw_word}ing", "type": "Noun"}],
                    "collocations": [f"use {raw_word}", f"common {raw_word}"],
                    "common_mistake_wrong": f"wrong {raw_word}",
                    "common_mistake_correct": f"correct {raw_word}",
                    "memory_hook": f"Remember {raw_word}.",
                    "quiz_question": f"What does {raw_word} mean?",
                    "quiz_options": options,
                    "quiz_correct_index": 0,
                }
            }
        _ai._request_json = _fake_request_json
        ai_words = words[:5]
        enriched = _ai.enrich_words(ai_words)
        assert all(_ai._is_rich_enough(enriched[w.id]) for w in ai_words)
        assert all(enriched[w.id].synonyms and enriched[w.id].antonyms for w in ai_words)
    finally:
        _ai._request_json = original_request_json
        object.__setattr__(SETTINGS, "cerebras_api_key", original_ai_key)
        object.__setattr__(SETTINGS, "use_cerebras", original_use_ai)

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
