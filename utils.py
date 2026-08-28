"""Small shared helpers used by bot.py, scraper.py, and db.py.

Nothing in this module depends on Telegram, SQLAlchemy, or nodriver, so it's
safe to import from anywhere without creating a dependency cycle.
"""

from __future__ import annotations

import re

# Telegram MarkdownV2 reserved characters that must be escaped in any
# dynamic string interpolated into a MarkdownV2 message.
_MDV2_RESERVED_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!])")

#: EAES admission numbers as actually issued: exactly 8 digits, no letters,
#: slashes, or hyphens. (Earlier drafts of this bot over-guessed the format —
#: this is the real one, confirmed against the live site.)
ADMISSION_NUMBER_RE = re.compile(r"^\d{8}$")

#: First names: letters, spaces, periods, and hyphens only (e.g. "Mary-Jane", "O.J.").
FIRST_NAME_RE = re.compile(r"^[A-Za-z\s.\-]{2,60}$")

#: Prefix used for the "stop tracking" inline-keyboard callback_data payload.
#: Kept short since Telegram caps callback_data at 64 bytes.
STOP_CALLBACK_PREFIX = "stop:"


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


def build_stop_callback_data(admission_number: str) -> str:
    """Builds the callback_data payload for a "Stop tracking" inline button.

    Kept as a small pure function (rather than inlined f-strings in bot.py)
    so the encode/decode pair can be unit tested without needing a real
    Telegram Update/CallbackQuery object.
    """
    return f"{STOP_CALLBACK_PREFIX}{admission_number}"


def parse_stop_callback_data(data: str) -> str | None:
    """Extracts the admission number from a "Stop tracking" callback payload.

    Returns None if `data` doesn't match the expected prefix, so callers can
    ignore callback_data they don't recognize instead of raising.
    """
    if not data or not data.startswith(STOP_CALLBACK_PREFIX):
        return None
    admission_number = data[len(STOP_CALLBACK_PREFIX):]
    return admission_number or None