import pytest

from utils import (
    ADMISSION_NUMBER_RE,
    FIRST_NAME_RE,
    build_stop_callback_data,
    escape_md_v2,
    mask,
    parse_stop_callback_data,
)


@pytest.mark.parametrize("value", ["12345678", "00000001", "99999999"])
def test_admission_number_accepts_valid(value):
    assert ADMISSION_NUMBER_RE.match(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "1234567",
        "123456789",
        "1234-567",
        "ET1234567",
        "AB/1234-56",
        "12 34567",
        "1234567 ",
    ],
)
def test_admission_number_rejects_invalid(value):
    assert not ADMISSION_NUMBER_RE.match(value)


@pytest.mark.parametrize(
    "value",
    ["Abebe", "Abebe Kebede", "Mary-Jane", "O.J.", "ab", "A" * 60],
)
def test_first_name_accepts_valid(value):
    assert FIRST_NAME_RE.match(value)


@pytest.mark.parametrize(
    "value",
    ["", "A", "A" * 61, "Abebe123", "Abebe!"],
)
def test_first_name_rejects_invalid(value):
    assert not FIRST_NAME_RE.match(value)


def test_mask_short_value_is_fully_masked():
    assert mask("12") == "**"
    assert mask("1234") == "****"


def test_mask_long_value_keeps_first_and_last_two_chars():
    result = mask("123456")
    assert result.startswith("12")
    assert result.endswith("56")
    assert "*" in result
    assert "3" not in result and "4" not in result


def test_mask_empty_string():
    assert mask("") == ""


def test_escape_md_v2_escapes_reserved_characters():
    assert escape_md_v2("Total: 98.5!") == "Total: 98\\.5\\!"


def test_escape_md_v2_handles_none():
    assert escape_md_v2(None) == ""


def test_escape_md_v2_leaves_plain_text_untouched():
    assert escape_md_v2("Abebe") == "Abebe"


def test_stop_callback_data_roundtrips():
    payload = build_stop_callback_data("12345678")
    assert parse_stop_callback_data(payload) == "12345678"


def test_stop_callback_data_stays_within_telegram_limit():
    payload = build_stop_callback_data("12345678")
    assert len(payload.encode("utf-8")) <= 64


def test_parse_stop_callback_data_rejects_unrelated_payload():
    assert parse_stop_callback_data("something_else:123") is None


def test_parse_stop_callback_data_rejects_empty_and_none():
    assert parse_stop_callback_data("") is None
    assert parse_stop_callback_data(None) is None
    assert parse_stop_callback_data("stop:") is None
