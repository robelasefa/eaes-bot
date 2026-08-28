"""
Database Management Layer
========================
Uses SQLAlchemy 2.0 (Async) to support SQLite for local testing
and PostgreSQL/MySQL for production deployments.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, delete, func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///tracking.db")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class TrackingRecord(Base):
    __tablename__ = "tracking"

    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admission_number: Mapped[str] = mapped_column(String(30), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


async def init_db() -> None:
    """Initializes database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency helper for acquiring async database sessions."""
    async with async_session_factory() as session:
        yield session


async def add_tracking(
    session: AsyncSession,
    chat_id: int,
    admission_number: str,
    first_name: str,
    max_limit: int = 10,
) -> str:
    """Adds a tracking record. Returns 'added', 'exists', or 'limit_reached'."""
    stmt_existing = select(TrackingRecord).where(
        TrackingRecord.chat_id == chat_id,
        TrackingRecord.admission_number == admission_number,
    )
    result = await session.execute(stmt_existing)
    if result.scalar_one_or_none():
        return "exists"

    stmt_count = select(func.count()).where(
        TrackingRecord.chat_id == chat_id, TrackingRecord.status == "active"
    )
    count = (await session.execute(stmt_count)).scalar_one()
    if count >= max_limit:
        return "limit_reached"

    record = TrackingRecord(
        chat_id=chat_id,
        admission_number=admission_number,
        first_name=first_name,
        status="active",
        attempts=0,
    )
    session.add(record)
    await session.commit()
    return "added"


async def remove_tracking(
    session: AsyncSession, chat_id: int, admission_number: str
) -> bool:
    """Removes a student tracking entry."""
    stmt = delete(TrackingRecord).where(
        TrackingRecord.chat_id == chat_id,
        TrackingRecord.admission_number == admission_number,
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount > 0


async def list_chat_tracking(
    session: AsyncSession, chat_id: int
) -> list[TrackingRecord]:
    """Retrieves all tracking entries for a given Telegram chat ID."""
    stmt = select(TrackingRecord).where(TrackingRecord.chat_id == chat_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_active_tracking(session: AsyncSession) -> list[TrackingRecord]:
    """Retrieves all active tracking records across all users."""
    stmt = select(TrackingRecord).where(TrackingRecord.status == "active")
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def increment_attempts(
    session: AsyncSession, chat_id: int, admission_number: str
) -> int:
    """Increments check attempts and returns updated count."""
    stmt = select(TrackingRecord).where(
        TrackingRecord.chat_id == chat_id,
        TrackingRecord.admission_number == admission_number,
    )
    record = (await session.execute(stmt)).scalar_one_or_none()
    if record:
        record.attempts += 1
        record.updated_at = datetime.now(timezone.utc)
        await session.commit()
        return record.attempts
    return 0
