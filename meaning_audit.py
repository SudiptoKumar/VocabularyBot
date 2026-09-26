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
UA = "VocabularyBot/1.2.0"


@dataclass(frozen=True)
class MeaningAudit:
    status: str  # valid, corrected, uncertain
    meaning_bn: str
    confidence: float
    reason: str = ""
    source_fingerprint: str = ""
    audited_at: str = ""


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


def _request_batch(words: list[WordEntry]) -> dict[str, Any] | None:
    records = [
        {
            "id": w.id,
            "word": w.term,
            "pronunciation_bn": w.pronunciation_bn,
            "ipa": w.ipa,
            "source_bangla_meaning": w.meaning_bn,
            "verb_hint": w.verb_hint,
        }
        for w in words
    ]
    schema = {
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
                        "valid": {"type": "boolean"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "corrected_bangla_meaning": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "valid", "confidence", "corrected_bangla_meaning", "reason"],
                },
            }
        },
        "required": ["items"],
    }
    system = """You are a strict bilingual vocabulary data auditor.

The supplied Bangla meaning is UNTRUSTED source data. Independently decide whether it matches the English headword and its likely part of speech. Do not assume the source meaning is correct.

Rules:
- A meaning is valid only when it is a natural Bangla meaning of the exact English headword and does not belong to another word.
- Obvious OCR/table misalignment is invalid, even if the Bangla phrase is grammatical.
- If valid=true, corrected_bangla_meaning should preserve the source meaning semantically.
- If valid=false, provide the correct common Bangla meaning(s) for the exact headword.
- Never invent a new sense just because another sense is possible; prefer the common sense matching the supplied pronunciation/context.
- Use bullet separators only between multiple meanings in corrected_bangla_meaning.
- Do not add a trailing danda or extra punctuation.
- confidence is your confidence in the valid/invalid judgment.
- Be conservative: if genuinely uncertain, set valid=false, keep confidence below 0.85, and explain why.
Return JSON only."""
    user = "WORDS:\n" + json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    payload = {
        "model": SETTINGS.cerebras_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "meaning_audit_v1", "strict": True, "schema": schema}},
        "max_completion_tokens": max(1200, 500 * len(words)),
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


def _fallback_audit(word: WordEntry) -> MeaningAudit:
    # Never silently rewrite source data when semantic verification is unavailable.
    return MeaningAudit(
        status="uncertain",
        meaning_bn=_clean_bn(word.meaning_bn),
        confidence=0.0,
        reason="Semantic meaning verification unavailable",
        source_fingerprint=_fingerprint(word.meaning_bn),
        audited_at=datetime.now(timezone.utc).isoformat(),
    )


def audit_words(words: list[WordEntry], cached: dict[str, Any] | None = None) -> dict[str, MeaningAudit]:
    cached = cached or {}
    result: dict[str, MeaningAudit] = {}
    pending: list[WordEntry] = []
    for word in words:
        fp = _fingerprint(word.meaning_bn)
        raw = cached.get(word.id)
        if isinstance(raw, dict) and raw.get("source_fingerprint") == fp:
            status = str(raw.get("status") or "uncertain")
            result[word.id] = MeaningAudit(
                status=status,
                meaning_bn=_clean_bn(raw.get("meaning_bn") or word.meaning_bn),
                confidence=float(raw.get("confidence") or 0.0),
                reason=_norm(raw.get("reason")),
                source_fingerprint=fp,
                audited_at=str(raw.get("audited_at") or ""),
            )
        else:
            pending.append(word)

    if pending and SETTINGS.use_cerebras and SETTINGS.cerebras_api_key:
        try:
            payload = _request_batch(pending) or {}
            items = payload.get("items", []) if isinstance(payload, dict) else []
            by_id = {str(x.get("id")): x for x in items if isinstance(x, dict) and x.get("id")}
            for word in pending:
                item = by_id.get(word.id)
                if not item:
                    result[word.id] = _fallback_audit(word)
                    continue
                try:
                    confidence = float(item.get("confidence", 0.0))
                except Exception:
                    confidence = 0.0
                valid = bool(item.get("valid"))
                corrected = _clean_bn(item.get("corrected_bangla_meaning") or "")
                source = _clean_bn(word.meaning_bn)
                if valid and confidence >= 0.85:
                    result[word.id] = MeaningAudit("valid", source, confidence, _norm(item.get("reason")), _fingerprint(word.meaning_bn), datetime.now(timezone.utc).isoformat())
                elif (not valid) and confidence >= 0.90 and corrected:
                    result[word.id] = MeaningAudit("corrected", corrected, confidence, _norm(item.get("reason")), _fingerprint(word.meaning_bn), datetime.now(timezone.utc).isoformat())
                else:
                    result[word.id] = MeaningAudit("uncertain", source, confidence, _norm(item.get("reason")), _fingerprint(word.meaning_bn), datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            logger.warning("Meaning audit batch failed; refusing silent correction: %s", exc)
            for word in pending:
                result[word.id] = _fallback_audit(word)
    else:
        for word in pending:
            result[word.id] = _fallback_audit(word)

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
