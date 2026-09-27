from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
import subprocess
from pathlib import Path

from config import AUDIO_DIR, SETTINGS

logger = logging.getLogger("vocabulary.audio")

AUDIO_PROFILE_VERSION = "1.2.4-standard-audio-dynamic-normal-speed"


def _estimate_syllables(word: str) -> int:
    """Small deterministic syllable estimate used only to choose an audio target."""
    value = re.sub(r"[^a-z]", "", word.lower())
    if not value:
        return 1
    groups = re.findall(r"[aeiouy]+", value)
    count = len(groups)
    if value.endswith("e") and not value.endswith(("le", "ye")) and count > 1:
        count -= 1
    if count <= 0:
        count = 1
    return count


def target_duration_seconds(word: str) -> float:
    """Choose a minimum playback bucket without changing speaking speed."""
    syllables = _estimate_syllables(word)
    if syllables <= 2:
        return 1.0
    if syllables == 3:
        return 1.5
    return 2.0


def _audio_path(word: str) -> Path:
    target = target_duration_seconds(word.strip())
    key_payload = f"{AUDIO_PROFILE_VERSION}|{SETTINGS.audio_voice}|{SETTINGS.audio_rate}|{target:.2f}|{word.strip()}"
    key = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()[:20]
    return AUDIO_DIR / f"{key}.mp3"


def _probe_duration(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not path.exists():
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        value = float(result.stdout.strip())
        return value if value > 0 else None
    except Exception as exc:
        logger.warning("ffprobe duration check failed for %s: %s", path, exc)
        return None


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
        subprocess.run(
            [espeak, "-v", "en-us", "-s", "165", "-w", str(wav), word],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(wav), "-codec:a", "libmp3lame", "-q:a", "5", str(output)],
            check=True,
            timeout=30,
        )
        return output.exists() and output.stat().st_size > 1000
    except Exception as exc:
        logger.warning("espeak fallback failed for %s: %s", word, exc)
        return False
    finally:
        try:
            wav.unlink(missing_ok=True)
        except Exception:
            pass


def _optimize_duration(source: Path, output: Path, target_seconds: float) -> bool:
    """Trim only dead air and pad silence to the target bucket; never change speech speed."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False

    trimmed = output.with_suffix(".trim.mp3")
    padded = output.with_suffix(".pad.mp3")
    for p in (trimmed, padded):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass

    try:
        subprocess.run(
            [
                ffmpeg, "-y", "-loglevel", "error",
                "-i", str(source),
                "-af",
                "silenceremove=start_periods=1:start_duration=0.03:start_threshold=-48dB:stop_periods=1:stop_duration=0.03:stop_threshold=-48dB",
                "-codec:a", "libmp3lame", "-q:a", "5",
                str(trimmed),
            ],
            check=True,
            timeout=30,
        )

        duration = _probe_duration(trimmed) or _probe_duration(source)
        if not duration:
            return False

        # Normal-speed speech is preserved. If naturally shorter than the
        # chosen bucket, add trailing silence so the file is never below 1s.
        # If naturally longer than the bucket, keep the natural duration.
        if duration >= target_seconds:
            trimmed.replace(output)
            return output.exists() and output.stat().st_size > 1000

        pad_target = target_seconds + 0.05
        pad_seconds = max(0.0, pad_target - duration)
        subprocess.run(
            [
                ffmpeg, "-y", "-loglevel", "error",
                "-i", str(trimmed),
                "-af", f"apad=pad_dur={pad_seconds:.3f}",
                "-t", f"{pad_target:.3f}",
                "-codec:a", "libmp3lame", "-q:a", "5",
                str(padded),
            ],
            check=True,
            timeout=30,
        )
        padded.replace(output)
        final_duration = _probe_duration(output)
        return bool(final_duration and final_duration >= 1.0 and output.stat().st_size > 1000)
    except Exception as exc:
        logger.warning("duration optimization failed for %s: %s", source, exc)
        return False
    finally:
        for p in (trimmed, padded):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

def ensure_audio(word: str) -> Path | None:
    if not SETTINGS.audio_enabled or not word.strip():
        return None

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    normalized = word.strip()
    output = _audio_path(normalized)
    if output.exists() and output.stat().st_size > 1000:
        return output

    source = output.with_suffix(".source.mp3")
    temp = output.with_suffix(".tmp.mp3")
    for p in (source, temp):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass

    if not (_edge_tts(normalized, temp) or _espeak_fallback(normalized, temp)):
        return None

    target = target_duration_seconds(normalized)
    try:
        if _optimize_duration(temp, output, target):
            return output
        # ffmpeg may not be available on a custom runner. In that case retain
        # the valid TTS output rather than failing the whole word.
        temp.replace(output)
        return output if output.exists() and output.stat().st_size > 1000 else None
    finally:
        try:
            source.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass
