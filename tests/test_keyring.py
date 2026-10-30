from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Keys must be in the environment BEFORE config builds SETTINGS.
os.environ["CEREBRAS_API_KEY"] = "test-key-1"
os.environ["CEREBRAS_API_KEY_2"] = "test-key-2"
os.environ["USE_CEREBRAS"] = "true"

import requests

import cerebras_keys
import content_ai
import meaning_audit
from config import SETTINGS

KEY1, KEY2 = "test-key-1", "test-key-2"


class FakeResponse:
    def __init__(self, status: int):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        return {"choices": [{"finish_reason": "stop", "message": {"content": '{"ok": true}'}}]}


class FakeTransport:
    """Routes requests.post by URL. Cerebras outcomes are scripted per key:
    each call pops the next scripted status for the key in the header."""

    def __init__(self, script: dict[str, list[int]]):
        self.script = {k: list(v) for k, v in script.items()}
        self.cerebras_auth: list[str] = []
        self.alerts: list[str] = []

    def __call__(self, url, **kwargs):
        if "api.telegram.org" in url:
            self.alerts.append(kwargs.get("data", {}).get("text", ""))
            return FakeResponse(200)
        auth = (kwargs.get("headers") or {}).get("Authorization", "")
        key = auth.replace("Bearer ", "")
        self.cerebras_auth.append(key)
        statuses = self.script.get(key) or [200]
        status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
        return FakeResponse(status)


def reset():
    cerebras_keys._reset_state()
    object.__setattr__(SETTINGS, "newsroom_bot_token", "")
    object.__setattr__(SETTINGS, "alert_chat_id", "")


def run_case(name, fn):
    reset()
    fn()
    print(f"PASS {name}")


def case_content_ai_failover():
    transport = FakeTransport({KEY1: [402], KEY2: [200]})
    original = requests.post
    requests.post = transport
    try:
        result = content_ai._request_json("sys", "user", {"type": "object"}, max_tokens=64)
    finally:
        requests.post = original
    assert result == {"ok": True}, result
    assert transport.cerebras_auth == [KEY1, KEY2], transport.cerebras_auth
    assert SETTINGS.cerebras_api_key == KEY2


def case_audit_failover():
    transport = FakeTransport({KEY1: [402], KEY2: [200]})
    original = requests.post
    requests.post = transport
    try:
        result = meaning_audit._post_json([{"role": "user", "content": "x"}], {"type": "object"}, max_tokens=64)
    finally:
        requests.post = original
    assert result == {"ok": True}, result
    assert transport.cerebras_auth == [KEY1, KEY2], transport.cerebras_auth
    assert SETTINGS.cerebras_api_key == KEY2


def case_non_quota_does_not_rotate():
    transport = FakeTransport({KEY1: [400], KEY2: [200]})
    original = requests.post
    requests.post = transport
    try:
        try:
            meaning_audit._post_json([{"role": "user", "content": "x"}], {"type": "object"}, max_tokens=64)
        except requests.HTTPError:
            pass
        else:
            raise AssertionError("400 should have raised")
    finally:
        requests.post = original
    assert transport.cerebras_auth == [KEY1], transport.cerebras_auth
    assert SETTINGS.cerebras_api_key == KEY1


def case_both_keys_dead():
    transport = FakeTransport({KEY1: [402], KEY2: [402]})
    original = requests.post
    requests.post = transport
    try:
        try:
            meaning_audit._post_json([{"role": "user", "content": "x"}], {"type": "object"}, max_tokens=64)
        except requests.HTTPError:
            pass
        else:
            raise AssertionError("dead keys should have raised")
    finally:
        requests.post = original
    assert transport.cerebras_auth == [KEY1, KEY2], transport.cerebras_auth


def case_alert_fires_once():
    object.__setattr__(SETTINGS, "newsroom_bot_token", "dummy-token")
    object.__setattr__(SETTINGS, "alert_chat_id", "12345")
    transport = FakeTransport({KEY1: [402], KEY2: [200]})
    original = requests.post
    requests.post = transport
    try:
        meaning_audit._post_json([{"role": "user", "content": "x"}], {"type": "object"}, max_tokens=64)
        # A second failover-triggering call in the same run must not re-alert
        # for key 1 (it is already active on key 2 and succeeds).
        meaning_audit._post_json([{"role": "user", "content": "y"}], {"type": "object"}, max_tokens=64)
    finally:
        requests.post = original
    assert len(transport.alerts) == 1, transport.alerts
    assert "VocabularyBot" in transport.alerts[0] and "key 1" in transport.alerts[0]


def run_all() -> None:
    run_case("content_ai failover 402 -> key 2", case_content_ai_failover)
    run_case("meaning_audit failover 402 -> key 2", case_audit_failover)
    run_case("non-quota 400 does not rotate", case_non_quota_does_not_rotate)
    run_case("both keys dead raises", case_both_keys_dead)
    run_case("alert fires exactly once", case_alert_fires_once)
    reset()
    print("keyring failover tests: PASS")


if __name__ == "__main__":
    run_all()
