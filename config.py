from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATE_DIR = BASE_DIR / "state"
STATE_FILE = STATE_DIR / "vocabulary_state.json"
GENERATED_DIR = BASE_DIR / "generated"
CARD_DIR = GENERATED_DIR / "cards"
AUDIO_DIR = GENERATED_DIR / "audio"
RUNTIME_DIR = BASE_DIR / ".runtime"
FONT_DIR = RUNTIME_DIR / "fonts"
ASSET_DIR = BASE_DIR / "assets"
LOG_DIR = BASE_DIR / "logs"


def env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_channel_id: str = os.getenv("TELEGRAM_CHANNEL_ID", "-1004330016419")
    channel_name: str = os.getenv("CHANNEL_NAME", "Vocabulary")
    channel_url: str = os.getenv("CHANNEL_URL", "")
    timezone: str = os.getenv("TIMEZONE", "Asia/Dhaka")
    words_per_run: int = env_int("WORDS_PER_RUN", 5)
    request_timeout: int = env_int("REQUEST_TIMEOUT", 45)
    telegram_retries: int = env_int("TELEGRAM_RETRIES", 3)
    state_branch: str = os.getenv("STATE_BRANCH", "vocabulary-state")
    stale_reservation_minutes: int = env_int("STALE_RESERVATION_MINUTES", 180)
    strict_dataset: bool = env_bool("STRICT_DATASET", True)

    cerebras_api_key: str = os.getenv("CEREBRAS_API_KEY", "")
    cerebras_model: str = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")
    cerebras_reasoning_effort: str = os.getenv("CEREBRAS_REASONING_EFFORT", "low")
    use_cerebras: bool = env_bool("USE_CEREBRAS", True)

    audio_enabled: bool = env_bool("AUDIO_ENABLED", True)
    audio_voice: str = os.getenv("AUDIO_VOICE", "en-US-JennyNeural")
    audio_rate: str = os.getenv("AUDIO_RATE", "+0%")

    quiz_enabled: bool = env_bool("QUIZ_ENABLED", True)
    quiz_per_word: bool = env_bool("QUIZ_PER_WORD", True)

    download_fonts: bool = env_bool("DOWNLOAD_FONTS", True)
    allow_font_fallback: bool = env_bool("ALLOW_FONT_FALLBACK", True)
    cache_generated_media: bool = env_bool("CACHE_GENERATED_MEDIA", True)

    protect_content: bool = env_bool("PROTECT_CONTENT", False)
    silent_posts: bool = env_bool("SILENT_POSTS", False)
    media_version: str = os.getenv("MEDIA_VERSION", "1.2.0")
    content_version: str = os.getenv("CONTENT_VERSION", "1.2.1")
    fail_closed_on_incomplete_content: bool = env_bool("FAIL_CLOSED_ON_INCOMPLETE_CONTENT", True)
    telegram_retry_unknown_outcome: bool = env_bool("TELEGRAM_RETRY_UNKNOWN_OUTCOME", False)
    telegram_preflight: bool = env_bool("TELEGRAM_PREFLIGHT", True)


SETTINGS = Settings()

REQUIRED_WORD_FIELDS = {
    "id", "term", "normalized_term", "pronunciation_bn", "ipa", "meaning_bn"
}
