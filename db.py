"""Async database layer (SQLAlchemy 2.0) for the EAES tracking bot.

Defaults to SQLite via aiosqlite; works unmodified against Postgres
(asyncpg) or MySQL (aiomysql/asyncmy) by just changing DATABASE_URL.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Index, Integer, String, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import DATABASE_URL, MAX_TRACKED_PER_CHAT

_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
_SessionLocal = async_sessionmaker(bind=_engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Tracking(Base):
    """One row per (chat, admission_number) pair being polled."""

    __tablename__ = "tracking"
    __table_args__ = (Index("idx_tracking_status", "status"),)

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    admission_number: Mapped[str] = mapped_column(String(8), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def init_db() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    await _engine.dispose()


def session_scope() -> AsyncSession:
    return _SessionLocal()


async def add_tracking(chat_id: int, admission_number: str, first_name: str) -> str:
    """Returns 'added', 'exists', or 'limit_reached'."""
    async with session_scope() as session, session.begin():
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
    async with session_scope() as session, session.begin():
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
            select(Tracking)
            .where(Tracking.status == "active")
            .order_by(Tracking.updated_at)
        )
        return list(result.scalars().all())


async def increment_attempts(chat_id: int, admission_number: str) -> int:
    async with session_scope() as session, session.begin():
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


async def delete_tracking(chat_id: int, admission_number: str) -> None:
    async with session_scope() as session, session.begin():
        await session.execute(
            delete(Tracking).where(
                Tracking.chat_id == chat_id,
                Tracking.admission_number == admission_number,
            )
        )
