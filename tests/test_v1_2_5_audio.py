from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich_message import build_rich_message
from dataset import load_all
from content_ai import fallback_word
from audio import target_duration_seconds

words = load_all()
w = next(x for x in words if x.normalized_term == "difference")
msg = build_rich_message(w, fallback_word(w), audio_media="attach://audio_test", audio_attach="audio_test")
audio = [b for b in msg["blocks"] if b.get("type") == "audio"][0]["audio"]
expected = w.term[:1].upper() + w.term[1:]
assert audio["title"] == expected
assert audio["performer"] == "Vocabulary"
assert audio["title"] == "Difference"
assert target_duration_seconds("buy") == 1.0
assert target_duration_seconds("difference") == 1.5
assert target_duration_seconds("vocabulary") == 2.0
assert not audio["title"].startswith("Vocabulary - ")
print("V1.2.5 audio title/duration rule test PASS")
