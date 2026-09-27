from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from config import DATA_DIR, SETTINGS


@dataclass(frozen=True)
class WordEntry:
    id: str
    term: str
    normalized_term: str
    pronunciation_bn: str
    ipa: str
    meaning_bn: str
    meaning_bn_photo: str = ""
    meaning_bn_post: str = ""
    verification_status: str = ""
    needs_review: bool = False
    source_numbers: list[int] = field(default_factory=list)
    source_chapters: list[int] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    verb_hint: bool = False
    sources: list[dict] = field(default_factory=list)


def _source_path() -> Path:
    return DATA_DIR / "vocabulary.json"


def load_all() -> list[WordEntry]:
    data = json.loads(_source_path().read_text(encoding="utf-8"))
    rows: list[WordEntry] = []
    for item in data.get("records", []):
        rows.append(
            WordEntry(
                id=str(item["id"]),
                term=str(item["term"]),
                normalized_term=str(item["normalized_term"]),
                pronunciation_bn=str(item.get("pronunciation_bn") or ""),
                ipa=str(item.get("ipa") or ""),
                meaning_bn=str(item.get("meaning_bn") or ""),
                meaning_bn_photo=str(item.get("meaning_bn_photo") or ""),
                meaning_bn_post=str(item.get("meaning_bn_post") or ""),
                verification_status=str((item.get("verification") or {}).get("status") or ""),
                needs_review=bool((item.get("verification") or {}).get("needs_review", False)),
                source_numbers=[int(x) for x in item.get("source_numbers", []) if isinstance(x, int) or str(x).isdigit()],
                source_chapters=[int(x) for x in item.get("source_chapters", []) if isinstance(x, int) or str(x).isdigit()],
                sections=[str(x) for x in item.get("sections", []) if str(x).strip()],
                verb_hint=bool(item.get("verb_hint", False)),
                sources=item.get("sources", []) if isinstance(item.get("sources"), list) else [],
            )
        )
    return rows


def dataset_fingerprint(words: list[WordEntry]) -> str:
    payload = "\n".join(f"{w.id}|{w.normalized_term}" for w in words)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_dataset(words: list[WordEntry], *, strict: bool = True) -> list[str]:
    findings: list[str] = []
    try:
        data = json.loads(_source_path().read_text(encoding="utf-8"))
        if str(data.get("schema_version")) != "2.0":
            findings.append(f"Expected vocabulary schema 2.0, got {data.get('schema_version')!r}")
    except Exception as exc:
        findings.append(f"Could not read vocabulary schema metadata: {exc}")
    if not words:
        findings.append("Vocabulary database is empty")
        return findings

    ids = set()
    terms = set()
    for index, word in enumerate(words, start=1):
        missing = [field for field in ("id", "term", "normalized_term") if not getattr(word, field)]
        if missing:
            findings.append(f"Record {index}: missing {', '.join(missing)}")
        if word.id in ids:
            findings.append(f"Duplicate word ID: {word.id}")
        ids.add(word.id)
        if word.normalized_term in terms:
            findings.append(f"Duplicate normalized term after preprocessing: {word.normalized_term}")
        terms.add(word.normalized_term)

        if word.ipa and len(word.ipa) > 100:
            findings.append(f"{word.id}: IPA is unusually long")

    if strict and len(words) < 3000:
        findings.append(f"Unexpectedly small vocabulary pool: {len(words)}")
    return findings


def production_eligible(words: list[WordEntry]) -> list[WordEntry]:
    """Return only records safe for live publication.

    Database V2 explicitly flags records that still need deeper review and keeps
    a few rows with no Bangla meaning. Those records remain in the master DB
    but are held out of the shuffle until a later database verification pass.
    """
    return [
        word for word in words
        if word.meaning_bn_photo
        and word.meaning_bn_post.endswith("।")
        and not word.needs_review
    ]
