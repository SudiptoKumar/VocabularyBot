from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from config import SETTINGS
from dataset import WordEntry

logger = logging.getLogger("vocabulary.meaning_audit")
CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
AUDIT_VERSION = "1.2.1"
UA = f"VocabularyBot/{AUDIT_VERSION}"


@dataclass(frozen=True)
class MeaningAudit:
    # valid = keep source unchanged
    # corrected = replace source with verified correction
    # uncertain = keep source unchanged, but flag for later review
    status: str
    meaning_bn: str
    confidence: float
    reason: str = ""
    source_fingerprint: str = ""
    audited_at: str = ""
    audit_version: str = AUDIT_VERSION


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _fingerprint(text: str) -> str:
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()


def _clean_bn(text: str) -> str:
    raw = _norm(text)
    raw = re.sub(r"[|]", "•", raw)
    raw = re.sub(r"\s*[•]+\s*", " • ", raw)
    raw = re.sub(r"\s*।\s*", " ", raw)
    raw = re.sub(r"\s*,\s*", ", ", raw)
    raw = re.sub(r"\s{2,}", " ", raw).strip(" ,•।")
    return raw


def _source_context(word: WordEntry) -> dict[str, Any]:
    return {
        "word": word.term,
        "ipa": word.ipa,
        "pronunciation_bn": word.pronunciation_bn,
        "source_bangla_meaning": word.meaning_bn,
        "verb_hint": word.verb_hint,
        "source_numbers": word.source_numbers,
        "source_chapters": word.source_chapters,
        "sections": word.sections,
        "sources": word.sources,
    }


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string"},
                        "decision": {"type": "string", "enum": ["keep", "correct", "uncertain"]},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "corrected_bangla_meaning": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "decision", "confidence", "corrected_bangla_meaning", "reason"],
                },
            }
        },
        "required": ["items"],
    }


def _judge_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decision": {"type": "string", "enum": ["keep", "correct", "uncertain"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "corrected_bangla_meaning": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["decision", "confidence", "corrected_bangla_meaning", "reason"],
    }


def _post_json(messages: list[dict[str, str]], schema: dict[str, Any], *, max_tokens: int) -> dict[str, Any]:
    payload = {
        "model": SETTINGS.cerebras_model,
        "messages": messages,
        "response_format": {"type": "json_schema", "json_schema": {"name": f"meaning_audit_{AUDIT_VERSION.replace('.', '_')}", "strict": True, "schema": schema}},
        "max_completion_tokens": max_tokens,
        "temperature": 0.0,
        "reasoning_effort": SETTINGS.cerebras_reasoning_effort,
    }
    headers = {
        "Authorization": f"Bearer {SETTINGS.cerebras_api_key}",
        "Content-Type": "application/json",
        "User-Agent": UA,
    }
    response = requests.post(CEREBRAS_URL, headers=headers, json=payload, timeout=SETTINGS.request_timeout)
    response.raise_for_status()
    body = response.json()
    choice = body["choices"][0]
    finish = str(choice.get("finish_reason") or "")
    if finish in {"length", "max_tokens"}:
        raise ValueError("Meaning audit response was truncated")
    raw = choice["message"]["content"]
    if isinstance(raw, dict):
        return raw
    raw = str(raw).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
    return json.loads(raw)


def _request_batch(words: list[WordEntry]) -> dict[str, Any] | None:
    records = [{"id": w.id, **_source_context(w)} for w in words]
    system = """You are a strict bilingual vocabulary data auditor.

The supplied Bangla meaning is UNTRUSTED source data. Determine whether it actually belongs to the exact English headword and the available source context.

Rules:
- Keep when the supplied Bangla meaning contains a valid common sense of this exact word. A source may legitimately contain multiple senses.
- Correct only when the meaning is clearly mismatched, corrupted, OCR-misaligned, or belongs to a different word.
- Do not mark a record uncertain merely because a word has multiple parts of speech or senses.
- For verbs, use verb_hint, source chapters, source term (for example 'to list'), and sections as supporting context.
- If one fragment is bad but another fragment is a valid sense, choose 'correct' only when you can confidently repair the whole meaning.
- If you cannot confidently distinguish keep vs correct, choose 'uncertain'.
- corrected_bangla_meaning must be clean Bengali meanings separated with the bullet ' • ' and no trailing punctuation.
- Never invent a rare sense merely to justify the source.
Return JSON only."""
    user = "WORDS:\n" + json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    return _post_json([{"role": "system", "content": system}, {"role": "user", "content": user}], _schema(), max_tokens=max(900, 420 * len(words)))


def _request_single_judge(word: WordEntry, primary: dict[str, Any]) -> dict[str, Any] | None:
    system = """You are the final semantic judge for one vocabulary record.

Your task is NOT to improve wording. Decide whether the existing Bangla meaning truly matches the exact English word.

Treat a source meaning as valid if at least one supplied sense is a normal meaning of the exact word in the relevant part of speech. Do not require every source fragment to be perfect when a clear correction can repair an OCR/table extraction error.

Output:
- keep: source meaning is trustworthy enough to retain.
- correct: the source meaning is clearly wrong or belongs to another word. Supply the common correct Bangla meaning(s).
- uncertain: evidence is insufficient. In this case the runtime must keep the original source meaning rather than inventing a correction.

Use the source context and the first audit decision as evidence, but independently judge it.
Return JSON only."""
    record = {
        "record": _source_context(word),
        "primary_audit": {
            "decision": primary.get("decision"),
            "confidence": primary.get("confidence"),
            "corrected_bangla_meaning": primary.get("corrected_bangla_meaning"),
            "reason": primary.get("reason"),
        },
    }
    user = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    return _post_json([{"role": "system", "content": system}, {"role": "user", "content": user}], _judge_schema(), max_tokens=700)


def _fallback_audit(word: WordEntry) -> MeaningAudit:
    return MeaningAudit(
        status="uncertain",
        meaning_bn=_clean_bn(word.meaning_bn),
        confidence=0.0,
        reason="Semantic verification unavailable; original source meaning retained",
        source_fingerprint=_fingerprint(word.meaning_bn),
        audited_at=datetime.now(timezone.utc).isoformat(),
    )


def _make_audit(word: WordEntry, decision: str, confidence: float, corrected: str, reason: str) -> MeaningAudit:
    source = _clean_bn(word.meaning_bn)
    now = datetime.now(timezone.utc).isoformat()
    if decision == "correct" and confidence >= 0.90 and corrected:
        return MeaningAudit("corrected", corrected, confidence, _norm(reason), _fingerprint(word.meaning_bn), now)
    if decision == "keep" and confidence >= 0.85:
        return MeaningAudit("valid", source, confidence, _norm(reason), _fingerprint(word.meaning_bn), now)
    return MeaningAudit("uncertain", source, confidence, _norm(reason), _fingerprint(word.meaning_bn), now)


def audit_words(words: list[WordEntry], cached: dict[str, Any] | None = None) -> dict[str, MeaningAudit]:
    cached = cached or {}
    result: dict[str, MeaningAudit] = {}
    pending: list[WordEntry] = []
    for word in words:
        fp = _fingerprint(word.meaning_bn)
        raw = cached.get(word.id)
        if (
            isinstance(raw, dict)
            and raw.get("source_fingerprint") == fp
            and str(raw.get("audit_version", "")) == AUDIT_VERSION
        ):
            status = str(raw.get("status") or "uncertain")
            result[word.id] = MeaningAudit(
                status=status,
                meaning_bn=_clean_bn(raw.get("meaning_bn") or word.meaning_bn),
                confidence=float(raw.get("confidence") or 0.0),
                reason=_norm(raw.get("reason")),
                source_fingerprint=fp,
                audited_at=str(raw.get("audited_at") or ""),
                audit_version=AUDIT_VERSION,
            )
        else:
            pending.append(word)

    if not pending:
        return result

    # Batch first for efficiency. Uncertain items receive a dedicated judge.
    primary_map: dict[str, dict[str, Any]] = {}
    if SETTINGS.use_cerebras and SETTINGS.cerebras_api_key:
        try:
            payload = _request_batch(pending) or {}
            items = payload.get("items", []) if isinstance(payload, dict) else []
            primary_map = {str(x.get("id")): x for x in items if isinstance(x, dict) and x.get("id")}
        except Exception as exc:
            logger.warning("Meaning audit batch failed; switching to per-word judges: %s", exc)

    for word in pending:
        primary = primary_map.get(word.id)
        if not primary:
            result[word.id] = _fallback_audit(word)
            continue
        try:
            decision = str(primary.get("decision") or "uncertain")
            confidence = float(primary.get("confidence") or 0.0)
            corrected = _clean_bn(primary.get("corrected_bangla_meaning") or "")
            reason = _norm(primary.get("reason"))
        except Exception:
            result[word.id] = _fallback_audit(word)
            continue

        # High-confidence decisions are accepted directly. Uncertain/borderline
        # decisions get a second independent semantic pass.
        if (decision == "keep" and confidence >= 0.90) or (decision == "correct" and confidence >= 0.95 and corrected):
            result[word.id] = _make_audit(word, decision, confidence, corrected, reason)
            continue

        judge = None
        try:
            judge = _request_single_judge(word, primary)
        except Exception as exc:
            logger.warning("Meaning judge failed for %s: %s", word.term, exc)
        if isinstance(judge, dict):
            j_decision = str(judge.get("decision") or "uncertain")
            try:
                j_conf = float(judge.get("confidence") or 0.0)
            except Exception:
                j_conf = 0.0
            j_corrected = _clean_bn(judge.get("corrected_bangla_meaning") or "")
            j_reason = _norm(judge.get("reason"))
            if j_decision == "correct" and j_conf >= 0.90 and j_corrected:
                result[word.id] = _make_audit(word, "correct", j_conf, j_corrected, j_reason)
                continue
            if j_decision == "keep" and j_conf >= 0.90:
                result[word.id] = _make_audit(word, "keep", j_conf, "", j_reason)
                continue

        # Best-safety behaviour for unresolved cases: retain the original source
        # rather than blocking the entire 5-word run or inventing a correction.
        result[word.id] = _make_audit(word, "uncertain", min(confidence, 0.84), corrected, reason or "Uncertain semantic match; original source retained")
    return result


def apply_audits(words: list[WordEntry], audits: dict[str, MeaningAudit]) -> list[WordEntry]:
    from dataclasses import replace
    out: list[WordEntry] = []
    for word in words:
        audit = audits.get(word.id)
        if audit and audit.status == "corrected":
            out.append(replace(word, meaning_bn=audit.meaning_bn))
        else:
            out.append(word)
    return out
