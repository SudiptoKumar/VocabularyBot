from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

import requests

from config import SETTINGS
from dataset import WordEntry

logger = logging.getLogger("vocabulary.ai")
CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
UA = "VocabularyBot/1.0.7"


@dataclass
class EnrichedWord:
    definition_en: str = ""
    short_meaning_en: str = ""
    part_of_speech: str = ""
    cefr: str = ""
    example_en: str = ""
    example_bn: str = ""
    synonyms: list[dict] | None = None
    antonyms: list[dict] | None = None
    word_family: list[dict] | None = None
    collocations: list[str] | None = None
    common_mistake_wrong: str = ""
    common_mistake_correct: str = ""
    memory_hook: str = ""
    quiz_question: str = ""
    quiz_options: list[str] | None = None
    quiz_correct_index: int = 0

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("synonyms", "antonyms", "word_family", "collocations", "quiz_options"):
            if data[key] is None:
                data[key] = []
        return data


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def infer_part_of_speech(word: WordEntry) -> str:
    """Deterministic safety fallback when AI is temporarily unavailable."""
    if word.verb_hint:
        return "Verb"
    term = word.normalized_term
    if term.startswith("to "):
        return "Verb"
    if term.endswith("ly") and len(term) > 4:
        return "Adverb"
    if term.endswith(("tion", "sion", "ment", "ness", "ity", "ance", "ence", "ship", "hood", "dom", "ism")):
        return "Noun"
    if term.endswith(("ous", "ful", "less", "able", "ible", "al", "ive", "ic", "ish")):
        return "Adjective"
    return "Word"


def fallback_word(word: WordEntry) -> EnrichedWord:
    return EnrichedWord(
        part_of_speech=infer_part_of_speech(word),
        short_meaning_en="",
        definition_en="",
        example_en="",
        example_bn="",
        synonyms=[],
        antonyms=[],
        word_family=[],
        collocations=[],
        common_mistake_wrong="",
        common_mistake_correct="",
        memory_hook=f"Remember: {word.term.upper()}",
        quiz_question=f"What is the meaning of {word.term}?",
        quiz_options=[],
        quiz_correct_index=0,
    )


def _item_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string"},
            "definition_en": {"type": "string"},
            "short_meaning_en": {"type": "string"},
            "part_of_speech": {"type": "string"},
            "cefr": {"type": "string"},
            "example_en": {"type": "string"},
            "example_bn": {"type": "string"},
            "synonyms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"word": {"type": "string"}, "meaning": {"type": "string"}},
                    "required": ["word", "meaning"],
                },
            },
            "antonyms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"word": {"type": "string"}, "meaning": {"type": "string"}},
                    "required": ["word", "meaning"],
                },
            },
            "word_family": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"word": {"type": "string"}, "type": {"type": "string"}},
                    "required": ["word", "type"],
                },
            },
            "collocations": {"type": "array", "items": {"type": "string"}},
            "common_mistake_wrong": {"type": "string"},
            "common_mistake_correct": {"type": "string"},
            "memory_hook": {"type": "string"},
            "quiz_question": {"type": "string"},
            "quiz_options": {"type": "array", "items": {"type": "string"}},
            "quiz_correct_index": {"type": "integer", "minimum": 0, "maximum": 3},
        },
        "required": [
            "id",
            "definition_en",
            "short_meaning_en",
            "part_of_speech",
            "cefr",
            "example_en",
            "example_bn",
            "synonyms",
            "antonyms",
            "word_family",
            "collocations",
            "common_mistake_wrong",
            "common_mistake_correct",
            "memory_hook",
            "quiz_question",
            "quiz_options",
            "quiz_correct_index",
        ],
    }


def _batch_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"items": {"type": "array", "items": _item_schema()}},
        "required": ["items"],
    }


def _build_prompt(words: list[WordEntry]) -> tuple[str, str]:
    records = [
        {
            "id": word.id,
            "word": word.term,
            "pronunciation_bn": word.pronunciation_bn,
            "ipa": word.ipa,
            "bangla_meaning": word.meaning_bn,
            "verb_hint": word.verb_hint,
        }
        for word in words
    ]
    system = """You are the vocabulary editor for a bilingual English-learning Telegram channel. Return JSON only.

Use the supplied Bangla meaning as the sense anchor. Do not invent an unrelated sense.
Use simple, natural English. Do not use markdown in any returned field.
Part of speech must be one main label such as noun, verb, adjective, adverb, preposition, pronoun, conjunction, determiner, interjection, phrase, or other.
CEFR must be A1, A2, B1, B2, C1 or C2. Make the best defensible estimate from the supplied word and sense.
short_meaning_en must be a very short English gloss of 1-3 words, suitable for a visual card, e.g. "result", "a tool", "nearby".
Definition should be one clear sentence.
Example should be one natural everyday sentence and example_bn its natural Bangla translation.
Return 2-4 real synonyms when they exist and match the intended sense. Return 1-3 real antonyms when they exist; otherwise [].
Return common word-family forms only, not unrelated derivatives.
Return 3-5 natural collocations.
Common mistake may be empty when no useful mistake exists.
Memory hook should aid recall without inventing false etymology.
Quiz must contain exactly 4 options and exactly one correct option; correct_index must point to that option.
Keep every field concise. Never omit a required field."""
    user = "INPUT WORDS:\n" + json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    return system, user


def _build_single_prompt(word: WordEntry) -> tuple[str, str]:
    system, _ = _build_prompt([word])
    user = (
        "Return exactly one item for this word. Use this source data:\n"
        + json.dumps(
            {
                "id": word.id,
                "word": word.term,
                "pronunciation_bn": word.pronunciation_bn,
                "ipa": word.ipa,
                "bangla_meaning": word.meaning_bn,
                "verb_hint": word.verb_hint,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return system, user


def _request_json(system: str, user: str, schema: dict, *, max_tokens: int, attempts: int = 2) -> dict | None:
    payload = {
        "model": SETTINGS.cerebras_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "vocabulary_enrichment_v1", "strict": True, "schema": schema},
        },
        "max_completion_tokens": max_tokens,
        "temperature": 0.1,
        "reasoning_effort": SETTINGS.cerebras_reasoning_effort,
    }
    headers = {
        "Authorization": f"Bearer {SETTINGS.cerebras_api_key}",
        "Content-Type": "application/json",
        "User-Agent": UA,
    }
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(CEREBRAS_URL, headers=headers, json=payload, timeout=SETTINGS.request_timeout)
            response.raise_for_status()
            body = response.json()
            choice = body["choices"][0]
            finish_reason = str(choice.get("finish_reason") or "")
            if finish_reason in {"length", "max_tokens"}:
                raise ValueError(f"Cerebras response truncated (finish_reason={finish_reason})")
            raw = choice["message"]["content"]
            if isinstance(raw, dict):
                return raw
            raw = str(raw).strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                first = raw.find("{")
                last = raw.rfind("}")
                if first >= 0 and last > first:
                    return json.loads(raw[first : last + 1])
                raise
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                logger.warning("AI JSON attempt %d/%d failed: %s", attempt, attempts, exc)
    if last_error:
        raise last_error
    return None


def _sanitize(item: dict, word: WordEntry) -> EnrichedWord:
    base = fallback_word(word)
    for key in (
        "definition_en",
        "short_meaning_en",
        "part_of_speech",
        "cefr",
        "example_en",
        "example_bn",
        "common_mistake_wrong",
        "common_mistake_correct",
        "memory_hook",
        "quiz_question",
    ):
        setattr(base, key, _norm(item.get(key, "")))

    if not base.part_of_speech:
        base.part_of_speech = infer_part_of_speech(word)
    if base.cefr not in {"A1", "A2", "B1", "B2", "C1", "C2"}:
        base.cefr = ""
    if not base.short_meaning_en:
        base.short_meaning_en = ""

    def clean_pairs(raw_value: Any, limit: int) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for x in raw_value if isinstance(raw_value, list) else []:
            if not isinstance(x, dict):
                continue
            w = _norm(x.get("word", ""))
            m = _norm(x.get("meaning", ""))
            key = w.lower()
            if not w or not m or key == word.normalized_term or key in seen:
                continue
            seen.add(key)
            out.append({"word": w, "meaning": m})
            if len(out) >= limit:
                break
        return out

    base.synonyms = clean_pairs(item.get("synonyms"), 4)
    base.antonyms = clean_pairs(item.get("antonyms"), 4)

    base.word_family = []
    seen_family: set[str] = set()
    for x in item.get("word_family", []) if isinstance(item.get("word_family"), list) else []:
        if not isinstance(x, dict):
            continue
        w = _norm(x.get("word", ""))
        t = _norm(x.get("type", ""))
        key = w.lower()
        if w and t and key != word.normalized_term and key not in seen_family:
            seen_family.add(key)
            base.word_family.append({"word": w, "type": t})
        if len(base.word_family) >= 5:
            break

    base.collocations = []
    seen_coll: set[str] = set()
    for x in item.get("collocations", []) if isinstance(item.get("collocations"), list) else []:
        s = _norm(x)
        key = s.lower()
        if s and key not in seen_coll:
            seen_coll.add(key)
            base.collocations.append(s)
        if len(base.collocations) >= 5:
            break

    options = item.get("quiz_options") if isinstance(item.get("quiz_options"), list) else []
    options = [_norm(x) for x in options if _norm(x)]
    dedup: list[str] = []
    seen = set()
    for opt in options:
        key = opt.lower()
        if key not in seen:
            seen.add(key)
            dedup.append(opt)
    if len(dedup) == 4:
        base.quiz_options = dedup
        try:
            idx = int(item.get("quiz_correct_index", 0))
        except Exception:
            idx = 0
        base.quiz_correct_index = max(0, min(3, idx))
    else:
        base.quiz_options = []
        base.quiz_correct_index = 0
    return base


def _is_rich_enough(value: EnrichedWord) -> bool:
    allowed_cefr = {"A1", "A2", "B1", "B2", "C1", "C2"}
    options = value.quiz_options or []
    return bool(
        value.definition_en
        and value.example_en
        and value.example_bn
        and value.short_meaning_en
        and 1 <= len(value.short_meaning_en.split()) <= 3
        and value.part_of_speech
        and value.part_of_speech.strip().lower() != "word"
        and value.cefr in allowed_cefr
        and value.memory_hook
        and value.quiz_question
        and len(options) == 4
        and all(str(opt).strip() for opt in options)
        and 0 <= int(value.quiz_correct_index) < 4
    )


def enrich_words(words: list[WordEntry]) -> dict[str, EnrichedWord]:
    results = {w.id: fallback_word(w) for w in words}
    if not words or not SETTINGS.use_cerebras or not SETTINGS.cerebras_api_key:
        return results

    # Fast path: one compact batch. This keeps normal runs efficient.
    try:
        system, user = _build_prompt(words)
        payload = _request_json(system, user, _batch_schema(), max_tokens=max(4000, 1400 * len(words)), attempts=2)
        items = payload.get("items", []) if isinstance(payload, dict) else []
        by_id = {str(x.get("id")): x for x in items if isinstance(x, dict) and x.get("id")}
        for word in words:
            item = by_id.get(word.id)
            if item:
                results[word.id] = _sanitize(item, word)
    except Exception as exc:
        logger.warning("Batch AI enrichment failed; switching to per-word recovery: %s", exc)

    # Recovery path: isolate words so one malformed/overlong response cannot blank the whole post.
    for word in words:
        current = results[word.id]
        if _is_rich_enough(current):
            continue
        try:
            system, user = _build_single_prompt(word)
            payload = _request_json(system, user, {"type": "object", "additionalProperties": False, "properties": {"item": _item_schema()}, "required": ["item"]}, max_tokens=2200, attempts=2)
            item = payload.get("item") if isinstance(payload, dict) else None
            if isinstance(item, dict) and str(item.get("id", "")) == word.id:
                candidate = _sanitize(item, word)
                if _is_rich_enough(candidate):
                    results[word.id] = candidate
                    continue
        except Exception as exc:
            logger.warning("Per-word AI enrichment failed for %s: %s", word.term, exc)
        logger.warning("Using safe fallback content for %s", word.term)

    return results
