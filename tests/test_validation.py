import pytest

from utils import ADMISSION_NUMBER_RE, FIRST_NAME_RE, escape_md_v2, mask


@pytest.mark.parametrize(
    "value",
    [
        "123456",
        "AB/1234-56",
        "12-34-56",
        "A1B2C3",
        "1" * 30,  # max length
        "abc",  # min length
    ],
)
def test_admission_number_accepts_valid(value):
    assert ADMISSION_NUMBER_RE.match(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "ab",  # too short
        "1" * 31,  # too long
        "12 34",  # spaces not allowed
        "abc@123",  # invalid char
        "abc#123",
    ],
)
def test_admission_number_rejects_invalid(value):
    assert not ADMISSION_NUMBER_RE.match(value)


@pytest.mark.parametrize(
    "value",
    [
        "Abebe",
        "Abebe Kebede",
        "Mary-Jane",
        "O.J.",
        "ab",  # min length
        "A" * 60,  # max length
    ],
)
def test_first_name_accepts_valid(value):
    assert FIRST_NAME_RE.match(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "A",  # too short
        "A" * 61,  # too long
        "Abebe123",  # digits not allowed
        "Abebe!",  # punctuation not allowed
    ],
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