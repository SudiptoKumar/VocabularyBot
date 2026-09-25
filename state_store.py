from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import STATE_DIR, STATE_FILE
from dataset import WordEntry, dataset_fingerprint

SCHEMA_VERSION = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fresh_state(words: list[WordEntry]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "pool_fingerprint": dataset_fingerprint(words),
        "cycle": 1,
        "queue_initialized": False,
        "remaining_ids": [],
        "reserved_runs": {},
        "published": {},
        "content_cache": {},
        "media_cache": {},
        "runs": [],
        "migrations": [],
    }


def _active_reserved_ids(state: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for run in (state.get("reserved_runs") or {}).values():
        if not isinstance(run, dict) or run.get("status") != "reserved":
            continue
        for item in run.get("items", []):
            if isinstance(item, dict) and item.get("id"):
                ids.add(str(item["id"]))
    return ids


def load_state(words: list[WordEntry]) -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        return fresh_state(words)

    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("state root is not an object")
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        backup = STATE_FILE.with_suffix(".corrupt.json")
        try:
            STATE_FILE.replace(backup)
        except OSError:
            pass
        return fresh_state(words)

    state = fresh_state(words)
    for key in state:
        if key in raw:
            state[key] = raw[key]

    old_schema = int(raw.get("schema_version", 1) or 1)
    current_ids = {w.id for w in words}
    old_fp = str(raw.get("pool_fingerprint") or "")
    new_fp = dataset_fingerprint(words)

    state["published"] = {
        k: v for k, v in (state.get("published") or {}).items() if k in current_ids
    }
    state["content_cache"] = {
        k: v for k, v in (state.get("content_cache") or {}).items() if k in current_ids
    }
    state["media_cache"] = {
        k: v for k, v in (state.get("media_cache") or {}).items() if k in current_ids
    }
    state["reserved_runs"] = state.get("reserved_runs") or {}

    # V2 and earlier filtered remaining_ids against the lifetime publication
    # ledger. That was incorrect because a completed word can be used again in
    # a later cycle. Preserve the queue, but remove only invalid/reserved IDs.
    reserved = _active_reserved_ids(state)
    remaining = [
        str(x)
        for x in (state.get("remaining_ids") or [])
        if str(x) in current_ids and str(x) not in reserved
    ]

    if old_schema < 3:
        # The old queue was authoritative before the schema-3 migration. If it
        # was already empty, the next run will initialize a fresh permutation.
        state["queue_initialized"] = bool(remaining)
    else:
        state["queue_initialized"] = bool(raw.get("queue_initialized", bool(remaining)))

    if old_fp != new_fp:
        known = set(remaining) | set(state["published"])
        known |= reserved
        for wid in current_ids - known:
            remaining.append(wid)
        state["migrations"] = (state.get("migrations") or [])[-19:]
        state["migrations"].append({
            "at": utc_now(),
            "from": old_fp,
            "to": new_fp,
            "reason": "dataset fingerprint changed",
        })

    state["schema_version"] = SCHEMA_VERSION
    state["pool_fingerprint"] = new_fp
    state["remaining_ids"] = remaining
    state["runs"] = (state.get("runs") or [])[-100:]
    state["migrations"] = (state.get("migrations") or [])[-20:]
    return state


def save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    normalized = copy.deepcopy(state)
    normalized["schema_version"] = SCHEMA_VERSION
    normalized["runs"] = (normalized.get("runs") or [])[-100:]
    normalized["migrations"] = (normalized.get("migrations") or [])[-20:]
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp.replace(STATE_FILE)
