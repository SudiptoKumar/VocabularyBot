from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataset import load_all, production_eligible


def test_v2_database_shape() -> None:
    words = load_all()
    assert len(words) == 3521
    ids = [w.id for w in words]
    assert len(ids) == len(set(ids))
    for w in production_eligible(words):
        assert w.meaning_bn_photo, f"missing photo meaning: {w.id} {w.term}"
        assert w.meaning_bn_post.endswith("।"), f"bad post meaning punctuation: {w.id} {w.term}"


def test_known_corrections() -> None:
    words = {w.term.lower(): w for w in load_all()}
    assert words["tall"].meaning_bn_photo == "লম্বা • উঁচু"
    assert words["tall"].meaning_bn_post == "লম্বা, উঁচু।"
    assert words["leadership"].meaning_bn_post == "নেতৃত্ব, পরিচালনা।"
    assert words["leadership"].ipa == "/ˈliː.də.ʃɪp/"
    assert words["leadership"].pronunciation_bn == "লিডারশিপ"
    assert words["list"].meaning_bn_post == "তালিকা, ফর্দ।"
