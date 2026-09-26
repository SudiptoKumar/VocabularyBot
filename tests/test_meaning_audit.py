from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meaning_audit import _clean_bn


def test_clean_bn_rules():
    assert _clean_bn("কেনা। | কিনতে") == "কেনা • কিনতে"
    assert _clean_bn("পাশে, নিকটে, বাইরে।") == "পাশে, নিকটে, বাইরে"


if __name__ == "__main__":
    test_clean_bn_rules()
    print("MEANING AUDIT TEST PASS")
