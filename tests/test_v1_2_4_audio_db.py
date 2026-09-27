from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
from pathlib import Path

import audio
from rich_message import build_rich_message
from dataset import load_all

ROOT = Path(__file__).resolve().parents[1]

def test_duration_buckets():
    assert audio.target_duration_seconds("buy") == 1.0
    assert audio.target_duration_seconds("century") == 1.5
    assert audio.target_duration_seconds("vocabulary") == 2.0

def test_no_speed_change_in_optimizer():
    src = (ROOT / "audio.py").read_text(encoding="utf-8")
    assert "atempo=" not in src
    assert "AUDIO_RATE" not in src[src.find("def _optimize_duration"):src.find("def ensure_audio")]

def test_century_pronunciation_database():
    words = load_all()
    century = next(w for w in words if w.normalized_term == "century")
    assert century.pronunciation_bn == "সেঞ্চুরি"
    assert century.ipa == "/ˈsen.tʃər.i/"
    assert century.meaning_bn_photo == "শতাব্দী • শতবর্ষ"
    assert century.meaning_bn_post == "শতাব্দী, শতবর্ষ।"

def test_audio_rich_block_title():
    words = load_all()
    century = next(w for w in words if w.normalized_term == "century")
    class E: pass
    e=E(); e.definition_en="A period of one hundred years."; e.short_meaning_en=""; e.example_en="A century is 100 years."; e.example_bn="এক শতাব্দী ১০০ বছর।"; e.synonyms=[]; e.antonyms=[]; e.word_family=[]; e.memory_hook="Remember century = 100 years."
    rich = build_rich_message(century, e, audio_media="AUDIO_FILE_ID")
    audio_blocks=[b for b in rich["blocks"] if b.get("type")=="audio"]
    assert len(audio_blocks)==1
    a=audio_blocks[0]["audio"]
    assert a["media"]=="AUDIO_FILE_ID"
    assert a["title"]=="Century"
    assert a["performer"]=="Vocabulary"
    assert a["performer"]=="Vocabulary"

def main():
    for fn in (test_duration_buckets,test_no_speed_change_in_optimizer,test_century_pronunciation_database,test_audio_rich_block_title): fn()
    print("V1.2.5 audio/database regression tests: PASS")

if __name__ == "__main__": main()
