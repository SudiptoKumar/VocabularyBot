from __future__ import annotations

import html
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


class TelegramAPIError(RuntimeError):
    """A confirmed Telegram API rejection. The request did not succeed."""


class TelegramUnknownOutcomeError(RuntimeError):
    """The client cannot know whether Telegram accepted the request."""


def _url(method: str) -> str:
    return f"https://api.telegram.org/bot{SETTINGS.telegram_bot_token}/{method}"


def _post(
    method: str,
    *,
    data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    retry_unknown_outcome: bool = True,
) -> dict[str, Any]:
    if not SETTINGS.telegram_bot_token.strip():
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    if not SETTINGS.telegram_channel_id.strip():
        raise RuntimeError("TELEGRAM_CHANNEL_ID is not configured.")

    last: Exception | None = None
    for attempt in range(1, SETTINGS.telegram_retries + 1):
        try:
            response = requests.post(_url(method), data=data, files=files, timeout=60)
        except requests.RequestException as exc:
            last = TelegramUnknownOutcomeError(
                f"Telegram {method} transport error; delivery outcome is unknown: {exc}"
            )
            logger.warning("Telegram %s attempt %d transport outcome unknown: %s", method, attempt, exc)
            if retry_unknown_outcome and attempt < SETTINGS.telegram_retries:
                time.sleep(min(2 ** (attempt - 1), 8))
                continue
            break

        try:
            payload = response.json()
        except ValueError as exc:
            last = TelegramUnknownOutcomeError(
                f"Telegram {method} returned a non-JSON response; delivery outcome is unknown: {exc}"
            )
            logger.warning("Telegram %s attempt %d response outcome unknown: %s", method, attempt, exc)
            if retry_unknown_outcome and attempt < SETTINGS.telegram_retries:
                time.sleep(min(2 ** (attempt - 1), 8))
                continue
            break

        if response.status_code >= 500:
            last = TelegramUnknownOutcomeError(
                f"Telegram {method} returned HTTP {response.status_code}; delivery outcome is unknown."
            )
            logger.warning("Telegram %s attempt %d server outcome unknown: HTTP %s", method, attempt, response.status_code)
            if retry_unknown_outcome and attempt < SETTINGS.telegram_retries:
                time.sleep(min(2 ** (attempt - 1), 8))
                continue
            break

        if not payload.get("ok"):
            params = payload.get("parameters") or {}
            retry_after = params.get("retry_after")
            if retry_after and attempt < SETTINGS.telegram_retries:
                time.sleep(min(int(retry_after), 60))
                continue
            raise TelegramAPIError(payload.get("description", "Telegram API error"))

        result = payload.get("result")
        if result is None:
            last = TelegramUnknownOutcomeError(
                f"Telegram {method} reported success without a result; delivery outcome is unknown."
            )
            break
        return result

    if last:
        raise last
    raise RuntimeError(f"Telegram {method} failed without a usable response.")


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
                retry_unknown_outcome=SETTINGS.telegram_retry_unknown_outcome,
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
        retry_unknown_outcome=SETTINGS.telegram_retry_unknown_outcome,
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
        data["explanation"] = html.escape(explanation[:200])
        data["explanation_parse_mode"] = "HTML"
    return _post("sendPoll", data=data, retry_unknown_outcome=SETTINGS.telegram_retry_unknown_outcome)


def preflight() -> dict[str, Any]:
    me = _post("getMe", data={}, retry_unknown_outcome=True)
    bot_id = int(me["id"])
    member = _post(
        "getChatMember",
        data={"chat_id": SETTINGS.telegram_channel_id, "user_id": bot_id},
        retry_unknown_outcome=True,
    )
    status = str(member.get("status", ""))
    if status not in {"administrator", "creator"}:
        raise RuntimeError(f"Telegram bot is not an administrator/creator in {SETTINGS.telegram_channel_id}: {status or 'unknown status'}")
    if status == "administrator" and member.get("can_post_messages") is False:
        raise RuntimeError("Telegram bot does not have permission to post messages in the configured channel.")
    return {"bot": me, "member": member}


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
                elif context in {"voice", "voice_note"} and "voice" not in found:
                    found["voice"] = obj["file_id"]
            for key, value in obj.items():
                next_context = context
                if key in {"photo", "audio", "voice", "voice_note"}:
                    next_context = key
                walk(value, next_context)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, context)

    walk(result)
    return found
