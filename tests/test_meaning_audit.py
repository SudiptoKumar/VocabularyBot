from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataset import load_all
from meaning_audit import _clean_bn, audit_words, apply_audits, MeaningAudit, AUDIT_VERSION


def test_clean_bn_rules():
    assert _clean_bn("কেনা। | কিনতে") == "কেনা • কিনতে"
    assert _clean_bn("পাশে, নিকটে, বাইরে।") == "পাশে, নিকটে, বাইরে"


def test_uncertain_is_non_blocking_and_keeps_source(monkeypatch):
    words = [next(w for w in load_all() if w.term.lower() == "list")]
    monkeypatch.setattr("meaning_audit.SETTINGS", type("S", (), {
        "use_cerebras": True, "cerebras_api_key": "x", "cerebras_model": "x",
        "cerebras_reasoning_effort": "low", "request_timeout": 1
    })())
    monkeypatch.setattr("meaning_audit._request_batch", lambda _: {"items": [{
        "id": words[0].id, "decision": "uncertain", "confidence": 0.72,
        "corrected_bangla_meaning": "তালিকা • ফর্দ", "reason": "ambiguous source row"
    }]})
    monkeypatch.setattr("meaning_audit._request_single_judge", lambda *_: {
        "decision": "uncertain", "confidence": 0.71,
        "corrected_bangla_meaning": "", "reason": "still ambiguous"
    })
    audits = audit_words(words, {})
    assert audits[words[0].id].status == "uncertain"
    applied = apply_audits(words, audits)
    assert applied[0].meaning_bn == words[0].meaning_bn


def test_clear_wrong_meaning_is_corrected(monkeypatch):
    words = [next(w for w in load_all() if w.term.lower() == "tall")]
    monkeypatch.setattr("meaning_audit.SETTINGS", type("S", (), {
        "use_cerebras": True, "cerebras_api_key": "x", "cerebras_model": "x",
        "cerebras_reasoning_effort": "low", "request_timeout": 1
    })())
    monkeypatch.setattr("meaning_audit._request_batch", lambda _: {"items": [{
        "id": words[0].id, "decision": "correct", "confidence": 0.98,
        "corrected_bangla_meaning": "লম্বা • উঁচু", "reason": "source meaning belongs to another word"
    }]})
    audits = audit_words(words, {})
    assert audits[words[0].id].status == "corrected"
    applied = apply_audits(words, audits)
    assert applied[0].meaning_bn == "লম্বা • উঁচু"


if __name__ == "__main__":
    test_clean_bn_rules()
    print("MEANING AUDIT TEST PASS")
