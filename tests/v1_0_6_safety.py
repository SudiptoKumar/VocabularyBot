from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import copy
import json
from datetime import datetime, timezone, timedelta

import content_ai as ai
import main as app
import telegram_client as tg
from config import SETTINGS
from content_ai import EnrichedWord, fallback_word
from dataset import load_all
from state_store import fresh_state
from shuffle_bag import recover_stale_reservations, reserve_next_batch


def rich() -> EnrichedWord:
    return EnrichedWord(
        definition_en="A clear definition.",
        short_meaning_en="clear meaning",
        part_of_speech="Noun",
        cefr="B1",
        example_en="This is a clear example.",
        example_bn="এটি একটি পরিষ্কার উদাহরণ।",
        synonyms=[{"word": "similar", "meaning": "nearly the same"}],
        antonyms=[{"word": "opposite", "meaning": "contrary"}],
        word_family=[{"word": "exampleful", "type": "Adjective"}],
        collocations=["use the word"],
        memory_hook="Remember this word.",
        quiz_question="What does it mean?",
        quiz_options=["A", "B", "C", "D"],
        quiz_correct_index=0,
    )


def test_cache_and_media() -> None:
    words = load_all()
    word = words[100]
    state = fresh_state(words)
    state["content_cache"][word.id] = fallback_word(word).to_dict() | {"_content_version": SETTINGS.content_version}
    assert word.id not in app._enrichment_from_cache(state, [word])
    state["content_cache"][word.id] = rich().to_dict() | {"_content_version": SETTINGS.content_version}
    assert word.id in app._enrichment_from_cache(state, [word])

    originals = (app.generate_card, app.ensure_audio, app.send_rich_message, app.send_poll, app.save_state)
    try:
        app.generate_card = lambda *a, **k: (_ for _ in ()).throw(AssertionError("card regenerated despite cache"))
        app.ensure_audio = lambda *a, **k: (_ for _ in ()).throw(AssertionError("audio regenerated despite cache"))
        app.send_rich_message = lambda *a, **k: {"message_id": 123}
        app.extract_media_ids = lambda result: {}
        app.send_poll = lambda *a, **k: {"message_id": 124}
        app.save_state = lambda state: None
        from shuffle_bag import reserve_next_batch
        reserve_next_batch(state, [w.id for w in words], 1, "media-test")
        state["reserved_runs"]["media-test"]["items"][0]["id"] = word.id
        state["media_cache"][word.id] = {
            "version": SETTINGS.media_version,
            "photo_file_id": "PHOTO123",
            "voice_file_id": "VOICE123",
        }
        assert app._publish_one(state, "media-test", word, rich(), 1, 1, 1, False)
    finally:
        app.generate_card, app.ensure_audio, app.send_rich_message, app.send_poll, app.save_state = originals


def test_fail_closed() -> None:
    words = load_all()
    originals = (app.preflight, app.save_state, app.load_state, app.enrich_words)
    try:
        app.preflight = lambda: None
        app.save_state = lambda state: None
        state = fresh_state(words)
        app.load_state = lambda _words: copy.deepcopy(state)
        app.enrich_words = lambda missing: {w.id: fallback_word(w) for w in missing}
        assert app.run(count=5, dry_run=False) == 2
    finally:
        app.preflight, app.save_state, app.load_state, app.enrich_words = originals


def test_poll_html_escape() -> None:
    calls = []
    original = tg._post
    try:
        tg._post = lambda method, **kwargs: calls.append((method, kwargs)) or {"message_id": 7}
        tg.send_poll("Q", ["A", "B", "C", "D"], 1, '<tag> & "quote"')
        assert calls[-1][1]["data"]["explanation"] == '&lt;tag&gt; &amp; &quot;quote&quot;'
    finally:
        tg._post = original


def test_cerebras_truncation_retry() -> None:
    class Resp:
        def __init__(self, payload):
            self.payload = payload
        def raise_for_status(self):
            return None
        def json(self):
            return self.payload

    calls = []
    original = ai.requests.post
    try:
        def fake_post(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return Resp({"choices": [{"finish_reason": "length", "message": {"content": "{"}}]})
            return Resp({"choices": [{"finish_reason": "stop", "message": {"content": '{"ok": true}'}}]})
        ai.requests.post = fake_post
        out = ai._request_json("s", "u", {"type": "object", "additionalProperties": False, "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}, max_tokens=100, attempts=2)
        assert out == {"ok": True}
        assert len(calls) == 2
    finally:
        ai.requests.post = original


def test_schema_budget() -> None:
    assert len(json.dumps(ai._batch_schema(), separators=(",", ":"), ensure_ascii=False)) < 5000


def test_media_fail_closed() -> None:
    words = load_all()
    word = words[50]
    state = fresh_state(words)
    run_id = "media-fail"
    reserve_next_batch(state, [w.id for w in words], 1, run_id)
    selected = [word]
    # Replace real reservation item with the target word so _publish_one sees it as reserved.
    state["reserved_runs"][run_id]["items"][0]["id"] = word.id
    originals = (app.generate_card, app.ensure_audio, app.save_state, app.send_rich_message)
    try:
        app.generate_card = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("font unavailable"))
        app.ensure_audio = lambda *a, **k: (_ for _ in ()).throw(AssertionError("audio should not be reached after card failure"))
        app.save_state = lambda state: None
        app.send_rich_message = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not publish incomplete card"))
        result = app._publish_one(state, run_id, word, rich(), 1, 1, 1, False)
        assert result is False
        assert word.id in state["reserved_runs"][run_id]["failed_ids"]
    finally:
        app.generate_card, app.ensure_audio, app.save_state, app.send_rich_message = originals


def test_audio_fail_closed() -> None:
    words = load_all()
    state = fresh_state(words)
    run_id = "audio-fail"
    picked = reserve_next_batch(state, [w.id for w in words], 1, run_id)
    word = next(w for w in words if w.id == picked[0]["id"])
    originals = (app.generate_card, app.ensure_audio, app.save_state, app.send_rich_message)
    try:
        card_path = Path("/tmp/card-audio-fail.png")
        card_path.write_bytes(b"x" * 3000)
        app.generate_card = lambda *a, **k: card_path
        app.ensure_audio = lambda *a, **k: None
        app.save_state = lambda state: None
        app.send_rich_message = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not publish incomplete media"))
        result = app._publish_one(state, run_id, word, rich(), 1, 1, 1, False)
        assert result is False
    finally:
        app.generate_card, app.ensure_audio, app.save_state, app.send_rich_message = originals
        Path("/tmp/card-audio-fail.png").unlink(missing_ok=True)


def test_unknown_outcome_is_quarantined_by_publish_path() -> None:
    words = load_all()
    state = fresh_state(words)
    run_id = "unknown-publish"
    picked = reserve_next_batch(state, [w.id for w in words], 1, run_id)
    word = next(w for w in words if w.id == picked[0]["id"])
    originals = (app.generate_card, app.ensure_audio, app.save_state, app.send_rich_message)
    original_audio = SETTINGS.audio_enabled
    try:
        app.generate_card = lambda *a, **k: Path("/tmp/card.png")
        app.ensure_audio = lambda *a, **k: Path("/tmp/audio.mp3")
        Path("/tmp/card.png").write_bytes(b"x" * 3000)
        Path("/tmp/audio.mp3").write_bytes(b"x" * 1200)
        app.save_state = lambda state: None
        from telegram_client import TelegramUnknownOutcomeError
        app.send_rich_message = lambda *a, **k: (_ for _ in ()).throw(TelegramUnknownOutcomeError("timeout after send"))
        object.__setattr__(SETTINGS, "audio_enabled", True)
        result = app._publish_one(state, run_id, word, rich(), 1, 1, 1, False)
        assert result is False
        assert word.id in state["reserved_runs"][run_id]["unknown_ids"]
        assert word.id not in state["remaining_ids"]
    finally:
        app.generate_card, app.ensure_audio, app.save_state, app.send_rich_message = originals
        object.__setattr__(SETTINGS, "audio_enabled", original_audio)
        Path("/tmp/card.png").unlink(missing_ok=True)
        Path("/tmp/audio.mp3").unlink(missing_ok=True)


def test_stale_reservation_is_quarantined() -> None:
    words = load_all()
    state = fresh_state(words)
    picked = reserve_next_batch(state, [w.id for w in words], 5, "stale-test")
    state["reserved_runs"]["stale-test"]["created_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=SETTINGS.stale_reservation_minutes + 1)
    ).isoformat()
    recovered = recover_stale_reservations(
        state, datetime.now(timezone.utc), SETTINGS.stale_reservation_minutes
    )
    recovered_set = set(recovered)
    assert recovered_set == {item["id"] for item in picked}
    assert state["reserved_runs"]["stale-test"]["status"] == "unknown_outcome"
    # Only unknown IDs remain blocked; successfully published siblings in an
    # old mixed run are not permanently quarantined.
    assert not recovered_set.intersection(set(state["remaining_ids"]))
    assert recovered_set.intersection({str(item["id"]) for item in state["reserved_runs"]["stale-test"]["items"]})


def test_mixed_run_only_unknown_ids_are_blocked() -> None:
    words = load_all()
    ids = [w.id for w in words]
    state = fresh_state(words)
    picked = reserve_next_batch(state, ids, 5, "mixed-outcome")
    from shuffle_bag import _reserved_ids, mark_published, mark_unknown, finish_run
    mark_published(state, "mixed-outcome", picked[0]["id"], 1, cycle=1)
    mark_published(state, "mixed-outcome", picked[1]["id"], 2, cycle=1)
    for item in picked[2:]:
        mark_unknown(state, "mixed-outcome", item["id"], "unknown")
    finish_run(state, "mixed-outcome")
    assert _reserved_ids(state) == {item["id"] for item in picked[2:]}


def test_state_corruption_fails_closed() -> None:
    import state_store as ss
    import tempfile
    from pathlib import Path
    original_state_file = ss.STATE_FILE
    original_state_dir = ss.STATE_DIR
    try:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            object.__setattr__(ss, "STATE_DIR", td_path)
            object.__setattr__(ss, "STATE_FILE", td_path / "vocabulary_state.json")
            ss.STATE_DIR.mkdir(parents=True, exist_ok=True)
            ss.STATE_FILE.write_text('{not valid json', encoding="utf-8")
            try:
                ss.load_state(load_all())
            except RuntimeError as exc:
                assert "refusing to publish with a fresh state" in str(exc)
            else:
                raise AssertionError("corrupt state must fail closed")
    finally:
        object.__setattr__(ss, "STATE_FILE", original_state_file)
        object.__setattr__(ss, "STATE_DIR", original_state_dir)


def test_http_5xx_is_unknown_outcome() -> None:
    original = tg.requests.post
    original_token = SETTINGS.telegram_bot_token
    class Resp:
        status_code = 500
        def json(self):
            return {"ok": False, "description": "Internal Server Error"}
    try:
        object.__setattr__(SETTINGS, "telegram_bot_token", "test-token")
        tg.requests.post = lambda *a, **k: Resp()
        try:
            tg._post("sendRichMessage", data={"chat_id": "-1001", "rich_message": "{}"}, retry_unknown_outcome=False)
        except tg.TelegramUnknownOutcomeError:
            pass
        else:
            raise AssertionError("HTTP 5xx must be treated as unknown outcome")
    finally:
        tg.requests.post = original
        object.__setattr__(SETTINGS, "telegram_bot_token", original_token)


if __name__ == "__main__":
    test_cache_and_media()
    test_fail_closed()
    test_poll_html_escape()
    test_cerebras_truncation_retry()
    test_schema_budget()
    test_media_fail_closed()
    test_audio_fail_closed()
    test_unknown_outcome_is_quarantined_by_publish_path()
    test_stale_reservation_is_quarantined()
    test_mixed_run_only_unknown_ids_are_blocked()
    test_state_corruption_fails_closed()
    test_http_5xx_is_unknown_outcome()
    print("V1.0.6 SAFETY REGRESSIONS PASS")
