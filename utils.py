"""Small shared helpers used by both bot.py and scraper.py."""

from __future__ import annotations

import re

# Telegram MarkdownV2 reserved characters that must be escaped in any
# dynamic string interpolated into a MarkdownV2 message.
_MDV2_RESERVED_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!])")

ADMISSION_NUMBER_RE = re.compile(r"^[A-Za-z0-9/\-]{3,30}$")
FIRST_NAME_RE = re.compile(r"^[A-Za-z\s.\-]{2,60}$")


def mask(value: str) -> str:
    """Mask a sensitive identifier for logging (never log full admission numbers)."""
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def escape_md_v2(text) -> str:
    """Escapes all Telegram MarkdownV2 reserved characters in dynamic text.

    Any string interpolated into a MarkdownV2 message must be passed through
    this first, or Telegram will reject the send (or worse, silently mangle
    the message) whenever the underlying text contains one of the reserved
    characters below.
    """
    if text is None:
        return ""
    return _MDV2_RESERVED_RE.sub(r"\\\1", str(text))