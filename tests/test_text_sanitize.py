from app.core.text import sanitize_json, sanitize_text


def test_sanitize_text_removes_nul_and_lone_surrogates() -> None:
    assert sanitize_text("a\x00b\ud800c") == "abc"


def test_sanitize_text_keeps_regular_characters() -> None:
    assert sanitize_text("函数\n极限\t二") == "函数\n极限\t二"


def test_sanitize_json_walks_nested_payloads() -> None:
    payload = {
        "title": "极限\x00",
        "slides": [{"content": {"text": "a\x00b"}, "points": ["x\x00", 3]}],
    }

    assert sanitize_json(payload) == {
        "title": "极限",
        "slides": [{"content": {"text": "ab"}, "points": ["x", 3]}],
    }
