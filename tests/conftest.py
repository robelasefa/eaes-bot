"""Shared pytest fixtures.

Sets required environment variables before any bot modules are imported,
since config.py reads os.environ at import time. Lets the suite run without
a real Telegram token and against a throwaway SQLite file instead of the
real tracking.db.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

# File-based (not :memory:) so every connection in the pool sees the same
# schema for the whole test session.
_tmp_db_path = Path(tempfile.mkdtemp(prefix="eaes_bot_test_")) / "test_tracking.db"
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
