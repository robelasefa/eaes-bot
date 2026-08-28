"""Central place for every environment-driven setting.

Importing this module has no side effects beyond reading os.environ, so it's
safe for db.py, scraper.py, and bot.py to all import from here without
creating import cycles.
"""

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

# Chrome/nodriver
CHROME_PROFILE_DIR = os.environ.get("EAES_CHROME_PROFILE_DIR", "./chrome_profile")
# Leave unset to let nodriver auto-detect. Set explicitly if you need to pin
# a specific Chrome/Chromium binary (common on Windows/macOS installs).
CHROME_EXECUTABLE_PATH = os.environ.get("EAES_CHROME_EXECUTABLE_PATH") or None
# On headless Linux servers there's no real display; set this to "1" to have
# scraper.py spin up a virtual one via pyvirtualdisplay instead of relying on
# an externally managed Xvfb process. No-op on Windows/macOS.
USE_VIRTUAL_DISPLAY = os.environ.get("EAES_USE_VIRTUAL_DISPLAY", "0") == "1"