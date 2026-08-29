"""Environment-driven settings, imported by db.py, scraper.py, and bot.py."""

from __future__ import annotations

import os

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///tracking.db")

EAES_URL = os.environ.get("EAES_URL", "https://result.eaes.et/")
POLL_INTERVAL_SECONDS = int(os.environ.get("EAES_POLL_INTERVAL_SECONDS", "300"))
MAX_CONCURRENT_CHECKS = int(os.environ.get("EAES_MAX_CONCURRENT", "3"))
MAX_TRACKED_PER_CHAT = int(os.environ.get("EAES_MAX_TRACKED_PER_CHAT", "10"))
MAX_ATTEMPTS = int(os.environ.get("EAES_MAX_ATTEMPTS", "50"))
FETCH_TIMEOUT_SECONDS = int(os.environ.get("EAES_FETCH_TIMEOUT_SECONDS", "60"))
JITTER_MIN_SECONDS = float(os.environ.get("EAES_JITTER_MIN_SECONDS", "0.5"))
JITTER_MAX_SECONDS = float(os.environ.get("EAES_JITTER_MAX_SECONDS", "2.5"))
BROWSER_HEALTH_CHECK_TIMEOUT_SECONDS = int(
    os.environ.get("EAES_BROWSER_HEALTH_CHECK_TIMEOUT_SECONDS", "15")
)

# Chrome/nodriver. On headless Linux, set EAES_USE_VIRTUAL_DISPLAY=1 to have
# scraper.py start a virtual display itself, or run under `xvfb-run`.
CHROME_PROFILE_DIR = os.environ.get("EAES_CHROME_PROFILE_DIR", "./chrome_profile")
CHROME_EXECUTABLE_PATH = os.environ.get("EAES_CHROME_EXECUTABLE_PATH") or None
USE_VIRTUAL_DISPLAY = os.environ.get("EAES_USE_VIRTUAL_DISPLAY", "0") == "1"