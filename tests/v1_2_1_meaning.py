from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from content_ai import EnrichedWord
from dataset import load_all
from meaning_audit import apply_audits, MeaningAudit, AUDIT_VERSION


def test_verified_meaning_is_a_real_wordentry():
    words = load_all()
    tall = next(w for w in words if w.term.lower() == "tall")
    audit = MeaningAudit(
        status="corrected",
        meaning_bn="লম্বা • উঁচু",
        confidence=0.99,
        reason="source mismatch",
        source_fingerprint="x",
        audited_at="now",
        audit_version=AUDIT_VERSION,
    )
    verified = apply_audits([tall], {tall.id: audit})[0]
    assert verified.meaning_bn == "লম্বা • উঁচু"
    assert verified.term == "tall"


def test_final_post_does_not_use_original_wrong_meaning():
    # Regression guard for the critical propagation bug fixed in V1.2.1:
    # once apply_audits changes a WordEntry, the publishing layer must use that
    # corrected object rather than re-indexing the original dataset.
    words = load_all()
    tall = next(w for w in words if w.term.lower() == "tall")
    audit = MeaningAudit(
        status="corrected",
        meaning_bn="লম্বা • উঁচু",
        confidence=0.99,
        reason="source mismatch",
        source_fingerprint="x",
        audited_at="now",
        audit_version=AUDIT_VERSION,
    )
    verified = apply_audits([tall], {tall.id: audit})
    by_id = {w.id: w for w in verified}
    assert by_id[tall.id].meaning_bn == "লম্বা • উঁচু"
    assert by_id[tall.id].meaning_bn != tall.meaning_bn


if __name__ == "__main__":
    test_verified_meaning_is_a_real_wordentry()
    test_final_post_does_not_use_original_wrong_meaning()
    print("V1.2.1 MEANING REGRESSION PASS")
