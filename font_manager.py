from __future__ import annotations

import logging
from pathlib import Path

import requests

from config import FONT_DIR, SETTINGS

logger = logging.getLogger("vocabulary.fonts")

FONT_URLS = {
    "playfair": "https://raw.githubusercontent.com/google/fonts/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf",
    "jakarta": "https://raw.githubusercontent.com/google/fonts/main/ofl/plusjakartasans/PlusJakartaSans%5Bwght%5D.ttf",
    "hind": "https://raw.githubusercontent.com/google/fonts/main/ofl/hindsiliguri/HindSiliguri-Regular.ttf",
    # Specimen-label card type (2026-11-12 rollout, same files as DailyFacts).
    "display": "https://fonts.gstatic.com/s/dmserifdisplay/v17/-nFnOHM81r4j6k0gjAW3mujVU2B2K_c.ttf",
    "archivo": "https://fonts.gstatic.com/s/archivo/v25/k3k6o8UDI-1M0wlSV9XAw6lQkqWY8Q82sJaRE-NWIDdgffTT0zRp8A.ttf",
    "archivo_medium": "https://fonts.gstatic.com/s/archivo/v25/k3k6o8UDI-1M0wlSV9XAw6lQkqWY8Q82sJaRE-NWIDdgffTTBjNp8A.ttf",
    "archivo_black": "https://fonts.gstatic.com/s/archivo/v25/k3k6o8UDI-1M0wlSV9XAw6lQkqWY8Q82sJaRE-NWIDdgffTTnTRp8A.ttf",
}


def ensure_fonts() -> dict[str, Path]:
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path] = {}
    for key, url in FONT_URLS.items():
        target = FONT_DIR / f"{key}.ttf"
        if target.exists() and target.stat().st_size > 10000:
            found[key] = target
            continue
        if not SETTINGS.download_fonts:
            continue
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "VocabularyBot/1.0"})
            r.raise_for_status()
            target.write_bytes(r.content)
            if target.stat().st_size > 10000:
                found[key] = target
        except Exception as exc:
            logger.warning("Could not download %s font: %s", key, exc)
    missing = [key for key in FONT_URLS if key not in found]
    if missing and SETTINGS.download_fonts and not SETTINGS.allow_font_fallback:
        raise RuntimeError("Required card fonts unavailable: " + ", ".join(missing))
    return found
