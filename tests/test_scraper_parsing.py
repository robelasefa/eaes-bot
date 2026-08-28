from scraper import parse_eaes_raw_text

SAMPLE_RESULT_TEXT = """
Abebe Kebede
Bole Secondary School
Admission No:
ET-2024-00123
Stream:
Natural Science
Sex:
M
English
78
Mathematics
92
Physics
88
Chemistry
81
Biology
75
TOTAL
414
AVG
82.8
"""


def test_parse_success_extracts_all_fields():
    message = parse_eaes_raw_text(SAMPLE_RESULT_TEXT)

    assert "Abebe Kebede" in message
    assert "Bole Secondary School" in message
    assert "ET\\-2024\\-00123" in message  # hyphens escaped for MarkdownV2
    assert "Natural Science" in message
    assert "414" in message
    assert "82\\.8" in message  # dot escaped
    assert "Mathematics" in message
    assert "92" in message


def test_parse_includes_all_known_subjects_present():
    message = parse_eaes_raw_text(SAMPLE_RESULT_TEXT)
    for subject in ["English", "Mathematics", "Physics", "Chemistry", "Biology"]:
        assert subject in message


def test_parse_missing_fields_falls_back_to_defaults():
    minimal_text = "TOTAL\n0\nAVG\n0"
    message = parse_eaes_raw_text(minimal_text)

    assert "Unknown" in message  # name/school default
    assert "N/A" in message  # admission_no/stream/gender default


def test_parse_output_is_valid_markdownv2_escaped():
    message = parse_eaes_raw_text(SAMPLE_RESULT_TEXT)
    # The header line's exclamation mark must be escaped, never raw.
    assert "Found\\!" in message
    assert "Found!" not in message


def test_parse_empty_input_does_not_raise():
    message = parse_eaes_raw_text("")
    assert "Unknown" in message
    assert "N/A" in message


def test_parse_ignores_unrecognized_subject_labels():
    text = "TOTAL\n50\nAVG\n50\nArt\n99"
    message = parse_eaes_raw_text(text)
    # "Art" isn't in the known_subjects set, so it should not appear as a
    # subject bullet.
    assert "Art" not in message
