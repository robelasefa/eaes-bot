"""
Async database layer (SQLAlchemy 2.0) for the EAES tracking bot.

Backed by ``DATABASE_URL``. Defaults to a local SQLite file via
``aiosqlite``, but works unmodified against PostgreSQL (``asyncpg``) or
MySQL (``aiomysql`` / ``asyncmy``) by simply changing that URL — no code
changes required.

Examples:
    sqlite+aiosqlite:///tracking.db
    postgresql+asyncpg://user:password@localhost:5432/eaes
    mysql+aiomysql://user:password@localhost:3306/eaes
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Index,
    Integer,
    String,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import DATABASE_URL, MAX_TRACKED_PER_CHAT

# `future=True`/2.0 style engine. `pool_pre_ping` guards against stale
# connections when running against Postgres/MySQL behind a load balancer or
# after a long idle period; it's a no-op for SQLite.
_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
_SessionLocal = async_sessionmaker(bind=_engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Tracking(Base):
    __tablename__ = "tracking"
    __table_args__ = (
        Index("idx_tracking_status", "status"),
    )

    # Composite primary key, mirroring the original schema: one row per
    # (chat, admission_number) pair.
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    admission_number: Mapped[str] = mapped_column(String(30), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose of the engine's connection pool (call on shutdown)."""
    await _engine.dispose()


def session_scope() -> AsyncSession:
    """Returns a new AsyncSession. Use as `async with session_scope() as s:`."""
    return _SessionLocal()


async def add_tracking(chat_id: int, admission_number: str, first_name: str) -> str:
    """Returns 'added', 'exists', or 'limit_reached'."""
    async with session_scope() as session:
        async with session.begin():
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(Tracking)
                    .where(Tracking.chat_id == chat_id, Tracking.status == "active")
                )
            ).scalar_one()

            existing = (
                await session.execute(
                    select(Tracking).where(
                        Tracking.chat_id == chat_id,
                        Tracking.admission_number == admission_number,
                    )
                )
            ).scalar_one_or_none()

            if existing is not None:
                return "exists"
            if count >= MAX_TRACKED_PER_CHAT:
                return "limit_reached"

            now = _now_iso()
            session.add(
                Tracking(
                    chat_id=chat_id,
                    admission_number=admission_number,
                    first_name=first_name,
                    status="active",
                    attempts=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            return "added"


async def remove_tracking(chat_id: int, admission_number: str) -> bool:
    async with session_scope() as session:
        async with session.begin():
            result = await session.execute(
                delete(Tracking).where(
                    Tracking.chat_id == chat_id,
                    Tracking.admission_number == admission_number,
                )
            )
            return result.rowcount > 0


async def list_for_chat(chat_id: int) -> list[Tracking]:
    async with session_scope() as session:
        result = await session.execute(
            select(Tracking)
            .where(Tracking.chat_id == chat_id)
            .order_by(Tracking.created_at)
        )
        return list(result.scalars().all())


async def list_active() -> list[Tracking]:
    async with session_scope() as session:
        result = await session.execute(
            select(Tracking).where(Tracking.status == "active").order_by(Tracking.updated_at)
        )
        return list(result.scalars().all())


async def increment_attempts(chat_id: int, admission_number: str) -> int:
    """Increments attempts and returns the new attempt count."""
    async with session_scope() as session:
        async with session.begin():
            await session.execute(
                update(Tracking)
                .where(
                    Tracking.chat_id == chat_id,
                    Tracking.admission_number == admission_number,
                )
                .values(attempts=Tracking.attempts + 1, updated_at=_now_iso())
            )
            row = (
                await session.execute(
                    select(Tracking.attempts).where(
                        Tracking.chat_id == chat_id,
                        Tracking.admission_number == admission_number,
                    )
                )
            ).scalar_one_or_none()
            return row or 0


# Old name kept as an alias so any external code (or your own tests) written
# against the previous function name keeps working.
record_attempt = increment_attempts


async def delete_tracking(chat_id: int, admission_number: str) -> None:
    async with session_scope() as session:
        async with session.begin():
            await session.execute(
                delete(Tracking).where(
                    Tracking.chat_id == chat_id,
                    Tracking.admission_number == admission_number,
                )
            )