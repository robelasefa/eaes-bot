"""Shared helpers used by bot.py, scraper.py, and db.py."""

from __future__ import annotations

import re

ADMISSION_NUMBER_RE = re.compile(
    r"^\d{8}$"
)  # admission numbers are 8 digits, no letters/dashes
FIRST_NAME_RE = re.compile(r"^[A-Za-z\s.\-]{2,60}$")  # allows "Mary-Jane", "O.J."

STOP_CALLBACK_PREFIX = "stop:"


def mask(value: str) -> str:
    """Mask a sensitive identifier for logging (never log full admission numbers)."""
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def build_stop_callback_data(admission_number: str) -> str:
    return f"{STOP_CALLBACK_PREFIX}{admission_number}"


def parse_stop_callback_data(data: str) -> str | None:
    if not data or not data.startswith(STOP_CALLBACK_PREFIX):
        return None
    return data[len(STOP_CALLBACK_PREFIX) :] or None
