import pytest

import db

pytestmark = pytest.mark.asyncio


async def test_add_tracking_returns_added_for_new_entry():
    outcome = await db.add_tracking(
        chat_id=1, admission_number="12345671", first_name="Abebe"
    )
    assert outcome == "added"

    rows = await db.list_for_chat(1)
    assert len(rows) == 1
    assert rows[0].admission_number == "12345671"
    assert rows[0].first_name == "Abebe"
    assert rows[0].status == "active"
    assert rows[0].attempts == 0


async def test_add_tracking_returns_exists_for_duplicate():
    await db.add_tracking(chat_id=1, admission_number="12345671", first_name="Abebe")
    outcome = await db.add_tracking(
        chat_id=1, admission_number="12345671", first_name="Abebe"
    )
    assert outcome == "exists"

    rows = await db.list_for_chat(1)
    assert len(rows) == 1


async def test_add_tracking_enforces_per_chat_limit(monkeypatch):
    monkeypatch.setattr(db, "MAX_TRACKED_PER_CHAT", 2)

    assert await db.add_tracking(1, "12345671", "A") == "added"
    assert await db.add_tracking(1, "12345672", "B") == "added"
    assert await db.add_tracking(1, "12345673", "C") == "limit_reached"

    rows = await db.list_for_chat(1)
    assert len(rows) == 2


async def test_different_chats_have_independent_limits(monkeypatch):
    monkeypatch.setattr(db, "MAX_TRACKED_PER_CHAT", 1)

    assert await db.add_tracking(1, "12345671", "A") == "added"
    assert await db.add_tracking(2, "12345671", "A") == "added"


async def test_remove_tracking_deletes_existing_row():
    await db.add_tracking(1, "12345671", "Abebe")
    removed = await db.remove_tracking(1, "12345671")
    assert removed is True
    assert await db.list_for_chat(1) == []


async def test_remove_tracking_returns_false_when_not_found():
    assert await db.remove_tracking(1, "does-not-exist") is False


async def test_list_active_only_returns_active_rows():
    await db.add_tracking(1, "12345671", "Abebe")
    await db.add_tracking(1, "12345672", "Chala")

    active = await db.list_active()
    admission_numbers = {row.admission_number for row in active}
    assert admission_numbers == {"12345671", "12345672"}


async def test_increment_attempts_increments_and_returns_count():
    await db.add_tracking(1, "12345671", "Abebe")

    first = await db.increment_attempts(1, "12345671")
    second = await db.increment_attempts(1, "12345671")

    assert first == 1
    assert second == 2

    rows = await db.list_for_chat(1)
    assert rows[0].attempts == 2


async def test_increment_attempts_on_missing_row_returns_zero():
    assert await db.increment_attempts(1, "does-not-exist") == 0


async def test_delete_tracking_removes_row():
    await db.add_tracking(1, "12345671", "Abebe")
    await db.delete_tracking(1, "12345671")
    assert await db.list_for_chat(1) == []


async def test_delete_tracking_is_idempotent():
    await db.delete_tracking(1, "does-not-exist")


async def test_list_for_chat_orders_by_created_at():
    await db.add_tracking(1, "12345671", "First")
    await db.add_tracking(1, "12345672", "Second")

    rows = await db.list_for_chat(1)
    assert [r.admission_number for r in rows] == ["12345671", "12345672"]
