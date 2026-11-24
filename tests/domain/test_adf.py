from scope_agent.domain.adf import adf_to_text, extract_ac

ADF_FIXTURE = {"type": "doc", "version": 1, "content": [
    {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Acceptance Criteria"}]},
    {"type": "bulletList", "content": [
        {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "flags load on startup"}]}]},
        {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "falls back to defaults"}]}]},
    ]},
]}


def test_adf_to_text():
    assert adf_to_text(ADF_FIXTURE) == "## Acceptance Criteria\n- flags load on startup\n- falls back to defaults"


def test_adf_to_text_none_and_empty():
    assert adf_to_text(None) == ""
    assert adf_to_text({"type": "doc", "content": []}) == ""


def test_extract_ac_heading_and_plain_label():
    assert extract_ac("Intro\n## Acceptance Criteria\n- a\n- b\n## Notes\nx", "Acceptance Criteria") == "- a\n- b"
    assert extract_ac("Intro\nAcceptance criteria:\n- a", "Acceptance Criteria") == "- a"


def test_extract_ac_missing():
    assert extract_ac("nothing here", "Acceptance Criteria") is None
