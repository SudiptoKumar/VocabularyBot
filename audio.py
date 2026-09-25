from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import subprocess
from pathlib import Path

from config import AUDIO_DIR, SETTINGS

logger = logging.getLogger("vocabulary.audio")


def _audio_path(word: str) -> Path:
    key = hashlib.sha256(f"{SETTINGS.audio_voice}|{SETTINGS.audio_rate}|{word}".encode("utf-8")).hexdigest()[:20]
    return AUDIO_DIR / f"{key}.mp3"


def _edge_tts(word: str, output: Path) -> bool:
    try:
        import edge_tts  # type: ignore
    except Exception:
        return False

    async def run() -> None:
        communicate = edge_tts.Communicate(word, SETTINGS.audio_voice, rate=SETTINGS.audio_rate)
        await communicate.save(str(output))

    try:
        asyncio.run(run())
        return output.exists() and output.stat().st_size > 1000
    except Exception as exc:
        logger.warning("edge-tts failed for %s: %s", word, exc)
        return False


def _espeak_fallback(word: str, output: Path) -> bool:
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    ffmpeg = shutil.which("ffmpeg")
    if not espeak or not ffmpeg:
        return False
    wav = output.with_suffix(".wav")
    try:
        subprocess.run([espeak, "-v", "en-us", "-s", "155", "-w", str(wav), word], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(wav), "-codec:a", "libmp3lame", "-q:a", "4", str(output)], check=True, timeout=30)
        return output.exists() and output.stat().st_size > 1000
    except Exception as exc:
        logger.warning("espeak fallback failed for %s: %s", word, exc)
        return False
    finally:
        try:
            wav.unlink(missing_ok=True)
        except Exception:
            pass


def ensure_audio(word: str) -> Path | None:
    if not SETTINGS.audio_enabled or not word.strip():
        return None
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    output = _audio_path(word.strip())
    if output.exists() and output.stat().st_size > 1000:
        return output
    temp = output.with_suffix(".tmp.mp3")
    try:
        temp.unlink(missing_ok=True)
    except Exception:
        pass
    if _edge_tts(word.strip(), temp) or _espeak_fallback(word.strip(), temp):
        temp.replace(output)
        return output
    return None
