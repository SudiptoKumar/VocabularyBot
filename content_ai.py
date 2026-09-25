from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict

import requests

from config import SETTINGS
from dataset import WordEntry

logger = logging.getLogger("vocabulary.ai")
CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
UA = "VocabularyBot/1.0"


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


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


def fallback_word(word: WordEntry) -> EnrichedWord:
    return EnrichedWord(
        part_of_speech="Verb" if word.verb_hint else "",
        short_meaning_en="",
        definition_en="",
        example_en="",
        example_bn="",
        synonyms=[],
        antonyms=[],
        word_family=[],
        collocations=[],
        memory_hook=f"Remember: {word.term.upper()}",
        quiz_question=f"What is the meaning of {word.term}?",
        quiz_options=[],
        quiz_correct_index=0,
    )


def _schema() -> dict:
    item = {
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
            "synonyms": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"word": {"type": "string"}, "meaning": {"type": "string"}}, "required": ["word", "meaning"]}, "maxItems": 5},
            "antonyms": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"word": {"type": "string"}, "meaning": {"type": "string"}}, "required": ["word", "meaning"]}, "maxItems": 5},
            "word_family": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"word": {"type": "string"}, "type": {"type": "string"}}, "required": ["word", "type"]}, "maxItems": 6},
            "collocations": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
            "common_mistake_wrong": {"type": "string"},
            "common_mistake_correct": {"type": "string"},
            "memory_hook": {"type": "string"},
            "quiz_question": {"type": "string"},
            "quiz_options": {"type": "array", "items": {"type": "string"}, "minItems": 4, "maxItems": 4},
            "quiz_correct_index": {"type": "integer", "minimum": 0, "maximum": 3},
        },
        "required": [
            "id", "definition_en", "short_meaning_en", "part_of_speech", "cefr",
            "example_en", "example_bn", "synonyms", "antonyms", "word_family",
            "collocations", "common_mistake_wrong", "common_mistake_correct",
            "memory_hook", "quiz_question", "quiz_options", "quiz_correct_index",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"items": {"type": "array", "items": item}},
        "required": ["items"],
    }


def _build_prompt(words: list[WordEntry]) -> tuple[str, str]:
    records = []
    for word in words:
        records.append({
            "id": word.id,
            "word": word.term,
            "bangla_pronunciation": word.pronunciation_bn,
            "ipa": word.ipa,
            "bangla_meaning": word.meaning_bn,
            "verb_hint": word.verb_hint,
            "sections": word.sections[:3],
        })

    system = """You are the vocabulary editor for a bilingual English-learning Telegram channel.
Return JSON only.

Source data rules:
- The supplied Bangla meaning is the authoritative meaning cue.
- Do not introduce a meaning that is unrelated to the supplied Bangla meaning.
- Keep definitions simple and learner-friendly.
- Use natural everyday English.
- For part_of_speech, choose one main label such as noun, verb, adjective, adverb, preposition, pronoun, conjunction, determiner, interjection, phrase, or other.
- CEFR is an estimate and must be one of A1, A2, B1, B2, C1, C2, or "" when uncertain.
- Synonyms and antonyms must genuinely match the intended sense. If none are safe, return an empty list.
- Word family must use real common forms only.
- Collocations must be natural English combinations.
- Common mistake may be empty when no common mistake is useful.
- Memory hook should help a learner remember the word, not invent a false etymology.
- The quiz must have exactly 4 options and exactly 1 correct option. Distractors must be plausible but clearly wrong.
- example_bn must naturally translate the example sentence into Bangla.
- Do not use markdown in returned fields.
- Keep output concise.
"""
    user = "INPUT WORDS:\n" + json.dumps(records, ensure_ascii=False, indent=2)
    return system, user


def _sanitize(item: dict, word: WordEntry) -> EnrichedWord:
    base = fallback_word(word)
    for key in [
        "definition_en", "short_meaning_en", "part_of_speech", "cefr", "example_en", "example_bn",
        "common_mistake_wrong", "common_mistake_correct", "memory_hook", "quiz_question",
    ]:
        value = _norm(item.get(key, ""))
        setattr(base, key, value)

    base.synonyms = []
    for x in item.get("synonyms", []) if isinstance(item.get("synonyms"), list) else []:
        if not isinstance(x, dict):
            continue
        w = _norm(x.get("word", ""))
        m = _norm(x.get("meaning", ""))
        if not w or not m or w.lower() == word.normalized_term:
            continue
        if w.lower() not in {z["word"].lower() for z in base.synonyms}:
            base.synonyms.append({"word": w, "meaning": m})
    base.synonyms = base.synonyms[:5]

    base.antonyms = []
    for x in item.get("antonyms", []) if isinstance(item.get("antonyms"), list) else []:
        if not isinstance(x, dict):
            continue
        w = _norm(x.get("word", ""))
        m = _norm(x.get("meaning", ""))
        if not w or not m or w.lower() == word.normalized_term:
            continue
        if w.lower() not in {z["word"].lower() for z in base.antonyms}:
            base.antonyms.append({"word": w, "meaning": m})
    base.antonyms = base.antonyms[:5]

    base.word_family = []
    for x in item.get("word_family", []) if isinstance(item.get("word_family"), list) else []:
        if not isinstance(x, dict):
            continue
        w = _norm(x.get("word", ""))
        t = _norm(x.get("type", ""))
        if w and t and w.lower() != word.normalized_term:
            base.word_family.append({"word": w, "type": t})
    base.word_family = base.word_family[:6]

    base.collocations = []
    for x in item.get("collocations", []) if isinstance(item.get("collocations"), list) else []:
        s = _norm(x)
        if s and s.lower() != word.normalized_term and s.lower() not in {z.lower() for z in base.collocations}:
            base.collocations.append(s)
    base.collocations = base.collocations[:6]

    options = item.get("quiz_options") if isinstance(item.get("quiz_options"), list) else []
    options = [_norm(x) for x in options if _norm(x)]
    dedup = []
    for opt in options:
        if opt.lower() not in {x.lower() for x in dedup}:
            dedup.append(opt)
    if len(dedup) == 4:
        base.quiz_options = dedup
        idx = item.get("quiz_correct_index", 0)
        try:
            idx = int(idx)
        except Exception:
            idx = 0
        base.quiz_correct_index = max(0, min(3, idx))
    else:
        base.quiz_options = []
        base.quiz_correct_index = 0
    return base


def enrich_words(words: list[WordEntry]) -> dict[str, EnrichedWord]:
    fallbacks = {w.id: fallback_word(w) for w in words}
    if not words or not SETTINGS.use_cerebras or not SETTINGS.cerebras_api_key:
        return fallbacks

    system, user = _build_prompt(words)
    payload = {
        "model": SETTINGS.cerebras_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "vocabulary_enrichment_v1", "strict": True, "schema": _schema()},
        },
        "max_completion_tokens": 3500,
        "temperature": 0.2,
    }
    try:
        response = requests.post(
            CEREBRAS_URL,
            headers={
                "Authorization": f"Bearer {SETTINGS.cerebras_api_key}",
                "Content-Type": "application/json",
                "User-Agent": UA,
            },
            json=payload,
            timeout=SETTINGS.request_timeout,
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        data = json.loads(raw)
        items = data.get("items", []) if isinstance(data, dict) else []
        by_id = {str(x.get("id")): x for x in items if isinstance(x, dict) and x.get("id")}
        for word in words:
            if word.id in by_id:
                fallbacks[word.id] = _sanitize(by_id[word.id], word)
        missing = [w.term for w in words if not fallbacks[w.id].definition_en]
        if missing:
            logger.warning("AI returned incomplete vocabulary content for: %s", ", ".join(missing))
    except (requests.RequestException, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("Cerebras enrichment failed; using safe fallbacks: %s", exc)
    return fallbacks
