from __future__ import annotations

import sys
from dataclasses import replace

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))

from content_ai import EnrichedWord
from dataset import load_all
from meaning_audit import apply_audits, MeaningAudit, AUDIT_VERSION


def test_verified_meaning_is_a_real_wordentry():
    words = load_all()
    tall = next(w for w in words if w.term.lower() == "tall")
    # Start with a simulated bad source record and verify that a confirmed
    # correction propagates into canonical, photo, and post representations.
    bad = replace(
        tall,
        meaning_bn="কথা বলা, বক্তৃতা দেওয়া",
        meaning_bn_photo="কথা বলা • বক্তৃতা দেওয়া",
        meaning_bn_post="কথা বলা, বক্তৃতা দেওয়া।",
    )
    audit = MeaningAudit(
        status="corrected",
        meaning_bn="লম্বা, উঁচু",
        confidence=0.99,
        reason="source mismatch",
        source_fingerprint="x",
        audited_at="now",
        audit_version=AUDIT_VERSION,
    )
    verified = apply_audits([bad], {bad.id: audit})[0]
    assert verified.meaning_bn == "লম্বা, উঁচু"
    assert verified.meaning_bn_photo == "লম্বা • উঁচু"
    assert verified.meaning_bn_post == "লম্বা, উঁচু।"
    assert verified.term == "tall"


def test_final_post_does_not_use_original_wrong_meaning():
    words = load_all()
    tall = next(w for w in words if w.term.lower() == "tall")
    bad = replace(
        tall,
        meaning_bn="কথা বলা, বক্তৃতা দেওয়া",
        meaning_bn_photo="কথা বলা • বক্তৃতা দেওয়া",
        meaning_bn_post="কথা বলা, বক্তৃতা দেওয়া।",
    )
    audit = MeaningAudit(
        status="corrected",
        meaning_bn="লম্বা, উঁচু",
        confidence=0.99,
        reason="source mismatch",
        source_fingerprint="x",
        audited_at="now",
        audit_version=AUDIT_VERSION,
    )
    verified = apply_audits([bad], {bad.id: audit})
    by_id = {w.id: w for w in verified}
    assert by_id[bad.id].meaning_bn_post == "লম্বা, উঁচু।"
    assert "কথা বলা" not in by_id[bad.id].meaning_bn_post


if __name__ == "__main__":
    test_verified_meaning_is_a_real_wordentry()
    test_final_post_does_not_use_original_wrong_meaning()
    print("V2 DATABASE MEANING REGRESSION PASS")
