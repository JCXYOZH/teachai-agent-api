from app.core.text import normalize_user_text, sanitize_json, sanitize_text


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



def test_normalize_user_text_collapses_single_line_fields() -> None:
    assert normalize_user_text("  极限\x00与\ud800连续  ", single_line=True) == "极限与连续"
    assert normalize_user_text("a\n\tb", single_line=True) == "a b"


def test_normalize_user_text_cleans_prose_but_keeps_paragraphs() -> None:
    value = "第一行\x00\r\n" + " " * 20 + "第二行\r\n\r\n\r\n第三行\x01  "

    assert normalize_user_text(value) == "第一行\n 第二行\n\n第三行"


def test_normalize_user_text_can_preserve_layout() -> None:
    value = "def f():\n    return 1\x00\n\n\n\nx = 2  "

    assert normalize_user_text(value, preserve_layout=True) == "def f():\n    return 1\n\nx = 2"
