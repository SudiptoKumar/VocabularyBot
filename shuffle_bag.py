from __future__ import annotations

import random
import secrets
from datetime import datetime, timezone
from typing import Any

from state_store import parse_dt, utc_now


def _shuffle(ids: list[str]) -> list[str]:
    """Return a cryptographically seeded random permutation."""
    rng = random.SystemRandom(secrets.randbits(64))
    out = list(ids)
    rng.shuffle(out)
    return out


def _new_cycle(state: dict[str, Any], all_ids: list[str]) -> None:
    state["cycle"] = int(state.get("cycle", 0)) + 1
    state["remaining_ids"] = _shuffle(all_ids)
    state["queue_initialized"] = True


def _reserved_ids(state: dict[str, Any]) -> set[str]:
    reserved: set[str] = set()
    for run in (state.get("reserved_runs") or {}).values():
        if not isinstance(run, dict) or run.get("status") != "reserved":
            continue
        for item in run.get("items", []):
            if isinstance(item, dict) and item.get("id"):
                reserved.add(str(item["id"]))
    return reserved


def ensure_queue(state: dict[str, Any], all_ids: list[str]) -> None:
    """Keep the persistent queue valid without consulting lifetime publication history.

    The queue itself is the no-repeat guarantee for the current cycle. Lifetime
    publication history is telemetry only. Filtering the queue by that history
    would incorrectly empty a brand-new cycle after the first full rotation.
    """
    all_set = set(all_ids)
    reserved = _reserved_ids(state)

    remaining = [
        str(x)
        for x in state.get("remaining_ids", [])
        if str(x) in all_set and str(x) not in reserved
    ]

    state["remaining_ids"] = remaining
    state["queue_initialized"] = bool(state.get("queue_initialized", False) or remaining)

    if remaining:
        return

    # First initialization: keep the configured cycle number.
    if not state["queue_initialized"]:
        state["remaining_ids"] = _shuffle(all_ids)
        state["queue_initialized"] = True
        return

    # If another run is still holding the last items of a cycle, do not advance
    # the cycle and accidentally create duplicates. The stale-reservation
    # recovery path will return those IDs after the configured timeout.
    if reserved:
        state["remaining_ids"] = []
        return

    # Current cycle exhausted. Start a fresh permutation.
    _new_cycle(state, all_ids)


def reserve_next_batch(state: dict[str, Any], all_ids: list[str], count: int, run_id: str) -> list[dict[str, Any]]:
    if count < 1:
        raise ValueError("count must be positive")

    selected: list[dict[str, Any]] = []
    while len(selected) < count:
        ensure_queue(state, all_ids)
        if not state.get("remaining_ids"):
            reserved = _reserved_ids(state)
            if reserved:
                raise RuntimeError(
                    "No vocabulary words are currently available because another reservation is still active."
                )
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
        pending = [
            str(x["id"])
            for x in run.get("items", [])
            if isinstance(x, dict) and str(x.get("id")) not in published_ids
        ]
        for wid in reversed(pending):
            if wid not in remaining:
                remaining.insert(0, wid)
        run["status"] = "recovered"
        run["recovered_at"] = utc_now()
        run["recovered_ids"] = pending
        run["failed_ids"] = sorted(set(run.get("failed_ids", [])))
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
    published_ids = run.setdefault("published_ids", [])
    if word_id not in published_ids:
        published_ids.append(word_id)
    state.setdefault("published", {})[word_id] = {
        "count": int(state["published"].get(word_id, {}).get("count", 0)) + 1,
        "last_cycle": int(cycle),
        "last_message_id": int(message_id),
        "published_at": utc_now(),
    }


def mark_failed(state: dict[str, Any], run_id: str, word_id: str, reason: str) -> None:
    run = _reservation_for(state, run_id)
    failed_ids = run.setdefault("failed_ids", [])
    if word_id not in failed_ids:
        failed_ids.append(word_id)
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
    if len(state.get("reserved_runs", {})) > 30:
        old = sorted(
            state["reserved_runs"].items(),
            key=lambda kv: str(kv[1].get("completed_at") or kv[1].get("created_at") or ""),
        )
        for rid, _ in old[:-30]:
            state["reserved_runs"].pop(rid, None)
