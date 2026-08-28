"""
Pytest Suite for Regex, DOM Parser, and Database CRUD
===================================================
Uses synthetic test data only.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db import (
    Base,
    add_tracking,
    increment_attempts,
    list_chat_tracking,
    remove_tracking,
)
from scraper import (
    ADMISSION_NUMBER_RE,
    FIRST_NAME_RE,
    escape_code_span,
    escape_md_v2,
    parse_eaes_raw_text,
)


def test_regex_validation():
    assert ADMISSION_NUMBER_RE.match("123456")
    assert ADMISSION_NUMBER_RE.match("ETH/999/23")
    assert not ADMISSION_NUMBER_RE.match("AB")

    assert FIRST_NAME_RE.match("Abebe")
    assert FIRST_NAME_RE.match("Kebede-Alemu")
    assert not FIRST_NAME_RE.match("12345")


def test_markdown_escaping():
    assert escape_md_v2("Hello. World!") == r"Hello\. World\!"
    assert escape_code_span("Ab.Deb-123") == "Ab.Deb-123"
    assert escape_code_span("code`with\\slash") == r"code\`with\\slash"


def test_dom_parsing():
    sample_raw_text = """
    Abebe Balcha
    Higa Model Boarding School
    Admission No:
    123456
    Stream:
    Natural Science
    Sex:
    M
    English
    88
    Mathematics
    95
    TOTAL
    580
    AVG
    82.8
    """
    output = parse_eaes_raw_text(sample_raw_text)

    assert "Abebe Balcha" in output
    assert "123456" in output
    assert "580" in output
    assert "88" in output


@pytest_asyncio.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_database_crud(async_session):
    res = await add_tracking(
        async_session, chat_id=1001, admission_number="123456", first_name="Abebe"
    )
    assert res == "added"

    res_dup = await add_tracking(
        async_session, chat_id=1001, admission_number="123456", first_name="Abebe"
    )
    assert res_dup == "exists"

    records = await list_chat_tracking(async_session, chat_id=1001)
    assert len(records) == 1
    assert records[0].first_name == "Abebe"

    attempts = await increment_attempts(
        async_session, chat_id=1001, admission_number="123456"
    )
    assert attempts == 1

    removed = await remove_tracking(
        async_session, chat_id=1001, admission_number="123456"
    )
    assert removed is True
