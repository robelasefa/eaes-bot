import pytest

import db

pytestmark = pytest.mark.asyncio


async def test_add_tracking_returns_added_for_new_entry():
    outcome = await db.add_tracking(
        chat_id=1, admission_number="ET-001", first_name="Abebe"
    )
    assert outcome == "added"

    rows = await db.list_for_chat(1)
    assert len(rows) == 1
    assert rows[0].admission_number == "ET-001"
    assert rows[0].first_name == "Abebe"
    assert rows[0].status == "active"
    assert rows[0].attempts == 0


async def test_add_tracking_returns_exists_for_duplicate():
    await db.add_tracking(chat_id=1, admission_number="ET-001", first_name="Abebe")
    outcome = await db.add_tracking(
        chat_id=1, admission_number="ET-001", first_name="Abebe"
    )
    assert outcome == "exists"

    rows = await db.list_for_chat(1)
    assert len(rows) == 1  # not duplicated


async def test_add_tracking_enforces_per_chat_limit(monkeypatch):
    monkeypatch.setattr(db, "MAX_TRACKED_PER_CHAT", 2)

    assert await db.add_tracking(1, "ET-001", "A") == "added"
    assert await db.add_tracking(1, "ET-002", "B") == "added"
    assert await db.add_tracking(1, "ET-003", "C") == "limit_reached"

    rows = await db.list_for_chat(1)
    assert len(rows) == 2


async def test_different_chats_have_independent_limits(monkeypatch):
    monkeypatch.setattr(db, "MAX_TRACKED_PER_CHAT", 1)

    assert await db.add_tracking(1, "ET-001", "A") == "added"
    assert (
        await db.add_tracking(2, "ET-001", "A") == "added"
    )  # different chat, same admission#


async def test_remove_tracking_deletes_existing_row():
    await db.add_tracking(1, "ET-001", "Abebe")
    removed = await db.remove_tracking(1, "ET-001")
    assert removed is True

    rows = await db.list_for_chat(1)
    assert rows == []


async def test_remove_tracking_returns_false_when_not_found():
    removed = await db.remove_tracking(1, "does-not-exist")
    assert removed is False


async def test_list_active_only_returns_active_rows():
    await db.add_tracking(1, "ET-001", "Abebe")
    await db.add_tracking(1, "ET-002", "Chala")

    active = await db.list_active()
    admission_numbers = {row.admission_number for row in active}
    assert admission_numbers == {"ET-001", "ET-002"}


async def test_increment_attempts_increments_and_returns_count():
    await db.add_tracking(1, "ET-001", "Abebe")

    first = await db.increment_attempts(1, "ET-001")
    second = await db.increment_attempts(1, "ET-001")

    assert first == 1
    assert second == 2

    rows = await db.list_for_chat(1)
    assert rows[0].attempts == 2


async def test_increment_attempts_on_missing_row_returns_zero():
    count = await db.increment_attempts(1, "does-not-exist")
    assert count == 0


async def test_delete_tracking_removes_row():
    await db.add_tracking(1, "ET-001", "Abebe")
    await db.delete_tracking(1, "ET-001")

    rows = await db.list_for_chat(1)
    assert rows == []


async def test_delete_tracking_is_idempotent():
    # Deleting a row that doesn't exist should not raise.
    await db.delete_tracking(1, "does-not-exist")


async def test_list_for_chat_orders_by_created_at():
    await db.add_tracking(1, "ET-001", "First")
    await db.add_tracking(1, "ET-002", "Second")

    rows = await db.list_for_chat(1)
    assert [r.admission_number for r in rows] == ["ET-001", "ET-002"]
