from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import STATE_DIR, STATE_FILE
from dataset import WordEntry, dataset_fingerprint

SCHEMA_VERSION = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fresh_state(words: list[WordEntry]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "pool_fingerprint": dataset_fingerprint(words),
        "cycle": 1,
        "remaining_ids": [],
        "reserved_runs": {},
        "published": {},
        "content_cache": {},
        "media_cache": {},
        "runs": [],
        "migrations": [],
    }


def load_state(words: list[WordEntry]) -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        state = fresh_state(words)
        return state
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("state root is not an object")
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        # Refuse to silently erase a possibly valid ledger. Keep a backup and start a safe fresh state.
        backup = STATE_FILE.with_suffix(".corrupt.json")
        try:
            STATE_FILE.replace(backup)
        except OSError:
            pass
        return fresh_state(words)

    state = fresh_state(words)
    state.update({k: raw.get(k, v) for k, v in state.items()})
    state["schema_version"] = SCHEMA_VERSION
    # Migrate against current pool without discarding publication history.
    current_ids = {w.id for w in words}
    old_fp = str(raw.get("pool_fingerprint") or "")
    new_fp = dataset_fingerprint(words)
    state["published"] = {k: v for k, v in (state.get("published") or {}).items() if k in current_ids}
    state["content_cache"] = {k: v for k, v in (state.get("content_cache") or {}).items() if k in current_ids}
    state["media_cache"] = {k: v for k, v in (state.get("media_cache") or {}).items() if k in current_ids}
    remaining = [x for x in (state.get("remaining_ids") or []) if x in current_ids and x not in state["published"]]
    if old_fp != new_fp:
        known = set(remaining) | set(state["published"])
        for wid in current_ids - known:
            remaining.append(wid)
        state["migrations"] = (state.get("migrations") or [])[-19:]
        state["migrations"].append({"at": utc_now(), "from": old_fp, "to": new_fp, "reason": "dataset fingerprint changed"})
    state["pool_fingerprint"] = new_fp
    state["remaining_ids"] = remaining
    state["reserved_runs"] = state.get("reserved_runs") or {}
    state["runs"] = (state.get("runs") or [])[-100:]
    return state


def save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    normalized = copy.deepcopy(state)
    normalized["schema_version"] = SCHEMA_VERSION
    normalized["runs"] = (normalized.get("runs") or [])[-100:]
    normalized["migrations"] = (normalized.get("migrations") or [])[-20:]
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(STATE_FILE)
