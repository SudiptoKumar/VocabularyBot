from __future__ import annotations

import json
import logging
import mimetypes
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import requests

from config import SETTINGS

logger = logging.getLogger("vocabulary.telegram")


def _url(method: str) -> str:
    return f"https://api.telegram.org/bot{SETTINGS.telegram_bot_token}/{method}"


def _post(method: str, *, data: dict[str, Any] | None = None, files: dict[str, Any] | None = None) -> dict[str, Any]:
    if not SETTINGS.telegram_bot_token.strip():
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    if not SETTINGS.telegram_channel_id.strip():
        raise RuntimeError("TELEGRAM_CHANNEL_ID is not configured.")

    last: Exception | None = None
    for attempt in range(1, SETTINGS.telegram_retries + 1):
        try:
            response = requests.post(_url(method), data=data, files=files, timeout=60)
            payload = response.json()
            if not payload.get("ok"):
                params = payload.get("parameters") or {}
                retry_after = params.get("retry_after")
                if retry_after and attempt < SETTINGS.telegram_retries:
                    time.sleep(min(int(retry_after), 60))
                    continue
                raise RuntimeError(payload.get("description", "Telegram API error"))
            return payload["result"]
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last = exc
            logger.warning("Telegram %s attempt %d failed: %s", method, attempt, exc)
            if attempt < SETTINGS.telegram_retries:
                time.sleep(min(2 ** (attempt - 1), 8))
    raise RuntimeError(f"Telegram {method} failed: {last}")


def _media_files(attachments: dict[str, Path]):
    stack = ExitStack()
    files = {}
    try:
        for attach_name, path in attachments.items():
            handle = stack.enter_context(open(path, "rb"))
            mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            files[attach_name] = (path.name, handle, mime)
        return stack, files
    except Exception:
        stack.close()
        raise


def send_rich_message(rich_message: dict[str, Any], attachments: dict[str, Path] | None = None) -> dict[str, Any]:
    attachments = attachments or {}
    if attachments:
        stack, files = _media_files(attachments)
        try:
            return _post(
                "sendRichMessage",
                data={
                    "chat_id": SETTINGS.telegram_channel_id,
                    "rich_message": json.dumps(rich_message, ensure_ascii=False),
                    "protect_content": str(SETTINGS.protect_content).lower(),
                    "disable_notification": str(SETTINGS.silent_posts).lower(),
                },
                files=files,
            )
        finally:
            stack.close()
    return _post(
        "sendRichMessage",
        data={
            "chat_id": SETTINGS.telegram_channel_id,
            "rich_message": json.dumps(rich_message, ensure_ascii=False),
            "protect_content": str(SETTINGS.protect_content).lower(),
            "disable_notification": str(SETTINGS.silent_posts).lower(),
        },
    )


def send_poll(question: str, options: list[str], correct_index: int, explanation: str = "") -> dict[str, Any]:
    data: dict[str, Any] = {
        "chat_id": SETTINGS.telegram_channel_id,
        "question": question[:300],
        "options": json.dumps([{"text": x[:100]} for x in options], ensure_ascii=False),
        "is_anonymous": "true",
        "shuffle_options": "false",
        "type": "quiz",
        "correct_option_ids": json.dumps([int(correct_index)]),
        "protect_content": str(SETTINGS.protect_content).lower(),
    }
    if explanation:
        data["explanation"] = explanation[:200]
        data["explanation_parse_mode"] = "HTML"
    return _post("sendPoll", data=data)


def diagnose() -> dict[str, Any]:
    return _post("getMe", data={})


def extract_media_ids(result: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}

    def walk(obj: Any, context: str = ""):
        if isinstance(obj, dict):
            if "file_id" in obj and isinstance(obj["file_id"], str):
                if context == "photo" and "photo" not in found:
                    found["photo"] = obj["file_id"]
                elif context == "audio" and "audio" not in found:
                    found["audio"] = obj["file_id"]
            for key, value in obj.items():
                next_context = context
                if key in {"photo", "audio", "voice"}:
                    next_context = key
                walk(value, next_context)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, context)

    walk(result)
    return found
