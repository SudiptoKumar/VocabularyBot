from __future__ import annotations

import random
import secrets
from datetime import datetime, timezone
from typing import Any

from state_store import parse_dt, utc_now


def _shuffle(ids: list[str]) -> list[str]:
    # Fisher-Yates through Python's secure seed. No persisted RNG state is required.
    rng = random.SystemRandom(secrets.randbits(64))
    out = list(ids)
    rng.shuffle(out)
    return out


def _new_cycle(state: dict[str, Any], all_ids: list[str]) -> None:
    state["cycle"] = int(state.get("cycle", 0)) + 1
    state["remaining_ids"] = _shuffle(all_ids)


def ensure_queue(state: dict[str, Any], all_ids: list[str]) -> None:
    all_set = set(all_ids)
    remaining = [x for x in state.get("remaining_ids", []) if x in all_set]
    published = set((state.get("published") or {}).keys())
    reserved = set()
    for run in (state.get("reserved_runs") or {}).values():
        if isinstance(run, dict):
            reserved.update(str(x.get("id")) for x in run.get("items", []) if x.get("id"))
    remaining = [x for x in remaining if x not in published and x not in reserved]
    state["remaining_ids"] = remaining
    if not remaining and len(published) < len(all_ids):
        unseen = [x for x in all_ids if x not in published and x not in reserved]
        if unseen:
            state["remaining_ids"] = _shuffle(unseen)


def reserve_next_batch(state: dict[str, Any], all_ids: list[str], count: int, run_id: str) -> list[dict[str, Any]]:
    if count < 1:
        raise ValueError("count must be positive")
    ensure_queue(state, all_ids)
    selected: list[dict[str, Any]] = []
    while len(selected) < count:
        if not state.get("remaining_ids"):
            _new_cycle(state, all_ids)
        take = min(count - len(selected), len(state["remaining_ids"]))
        cycle = int(state.get("cycle", 1))
        chunk = state["remaining_ids"][:take]
        state["remaining_ids"] = state["remaining_ids"][take:]
        selected.extend({"id": wid, "cycle": cycle} for wid in chunk)
    state.setdefault("reserved_runs", {})[run_id] = {
        "status": "reserved",
        "created_at": utc_now(),
        "items": selected,
        "published_ids": [],
        "failed_ids": [],
    }
    return selected


def recover_stale_reservations(state: dict[str, Any], now: datetime, stale_minutes: int) -> list[str]:
    recovered: list[str] = []
    reservations = state.setdefault("reserved_runs", {})
    remaining = state.setdefault("remaining_ids", [])
    for run_id, run in list(reservations.items()):
        if not isinstance(run, dict) or run.get("status") != "reserved":
            continue
        created = run.get("created_at")
        if not created:
            continue
        try:
            age_min = (now - parse_dt(str(created))).total_seconds() / 60
        except Exception:
            continue
        if age_min < stale_minutes:
            continue
        published_ids = set(run.get("published_ids", []))
        failed_ids = set(run.get("failed_ids", []))
        pending = [str(x["id"]) for x in run.get("items", []) if str(x.get("id")) not in published_ids]
        for wid in reversed(pending):
            if wid not in remaining:
                remaining.insert(0, wid)
        run["status"] = "recovered"
        run["recovered_at"] = utc_now()
        run["recovered_ids"] = pending
        run["failed_ids"] = sorted(failed_ids)
        recovered.extend(pending)
    return recovered


def _reservation_for(state: dict[str, Any], run_id: str) -> dict[str, Any]:
    run = (state.get("reserved_runs") or {}).get(run_id)
    if not isinstance(run, dict):
        raise KeyError(f"Unknown reservation {run_id}")
    return run


def mark_published(state: dict[str, Any], run_id: str, word_id: str, message_id: int, *, cycle: int) -> None:
    run = _reservation_for(state, run_id)
    if word_id not in {x.get("id") for x in run.get("items", [])}:
        raise KeyError(f"Word {word_id} not reserved by {run_id}")
    run.setdefault("published_ids", []).append(word_id) if word_id not in run.setdefault("published_ids", []) else None
    state.setdefault("published", {})[word_id] = {
        "count": int(state["published"].get(word_id, {}).get("count", 0)) + 1,
        "last_cycle": cycle,
        "last_message_id": int(message_id),
        "published_at": utc_now(),
    }


def mark_failed(state: dict[str, Any], run_id: str, word_id: str, reason: str) -> None:
    run = _reservation_for(state, run_id)
    if word_id not in run.setdefault("failed_ids", []):
        run["failed_ids"].append(word_id)
    remaining = state.setdefault("remaining_ids", [])
    if word_id not in remaining:
        remaining.insert(0, word_id)
    run.setdefault("failures", {})[word_id] = str(reason)[:1000]


def finish_run(state: dict[str, Any], run_id: str) -> None:
    run = _reservation_for(state, run_id)
    total = {x.get("id") for x in run.get("items", [])}
    done = set(run.get("published_ids", [])) | set(run.get("failed_ids", []))
    run["status"] = "completed" if total == done else "partial"
    run["completed_at"] = utc_now()
    summary = {
        "run_id": run_id,
        "created_at": run.get("created_at"),
        "completed_at": run.get("completed_at"),
        "status": run.get("status"),
        "items": run.get("items", []),
        "published_ids": run.get("published_ids", []),
        "failed_ids": run.get("failed_ids", []),
    }
    state.setdefault("runs", []).append(summary)
    # Keep a compact recent reservation history.
    if len(state.get("reserved_runs", {})) > 30:
        old = sorted(
            state["reserved_runs"].items(),
            key=lambda kv: str(kv[1].get("completed_at") or kv[1].get("created_at") or ""),
        )
        for rid, _ in old[:-30]:
            state["reserved_runs"].pop(rid, None)
