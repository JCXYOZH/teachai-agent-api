from typing import Any, Dict, List, Tuple

from app.services.extraction import _limit_sections


def test_sections_drop_nul_bytes() -> None:
    raw: List[Tuple[Dict[str, Any], str]] = [
        ({"kind": "page", "index": 1, "label": "page 1"}, "hello\x00world")
    ]

    extracted = _limit_sections(raw, 1, 1000)

    assert extracted.text == "helloworld"
    assert extracted.sections[0]["text"] == "helloworld"
    assert extracted.sections[0]["char_count"] == 10


def test_sections_drop_lone_surrogates() -> None:
    raw: List[Tuple[Dict[str, Any], str]] = [
        ({"kind": "page", "index": 1, "label": "page 1"}, "a\ud800b")
    ]

    extracted = _limit_sections(raw, 1, 1000)

    assert extracted.text == "ab"