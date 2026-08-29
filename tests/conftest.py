"""Shared pytest fixtures.

Sets required environment variables *before* any of the bot's modules are
imported, since config.py reads os.environ at import time. This lets the
test suite run without a real Telegram token and against an isolated,
throwaway SQLite database file instead of the real tracking.db.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Make the project root importable (tests/ sits one level below it).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Dummy token: config.py just needs a truthy value; nothing in the test
# suite talks to the real Telegram API.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

# A dedicated, file-based SQLite database for the whole test session so the
# suite never touches a real tracking.db, and so every test sees the same
# schema/connection pool (an actual `:memory:` URL would give each new
# connection its own empty database).
_tmp_db_dir = tempfile.mkdtemp(prefix="eaes_bot_test_")
_tmp_db_path = Path(_tmp_db_dir) / "test_tracking.db"
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_tmp_db_path}")

import pytest_asyncio

import db as db_module


@pytest_asyncio.fixture(autouse=True)
async def _fresh_schema():
    """Recreate the schema before every test so tests don't leak state."""
    async with db_module._engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.drop_all)
        await conn.run_sync(db_module.Base.metadata.create_all)
    yield