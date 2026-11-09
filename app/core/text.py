import re
from typing import Any

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_MULTI_SPACE = re.compile(r"[^\S\n]{3,}")
_MULTI_NEWLINE = re.compile(r"\n{3,}")


def sanitize_text(value: str) -> str:
    """Remove characters a PostgreSQL text column cannot store.

    NUL bytes break asyncpg with CharacterNotInRepertoireError, and a
    lone surrogate cannot be encoded as UTF-8 at all.
    """
    if "\x00" in value:
        value = value.replace("\x00", "")
    return value.encode("utf-8", "ignore").decode("utf-8")


def sanitize_json(value: Any) -> Any:
    """Recursively sanitize every string inside a JSON-like payload."""
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {
            sanitize_text(key) if isinstance(key, str) else key: sanitize_json(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_json(item) for item in value]
    return value


def normalize_user_text(
    value: str,
    *,
    single_line: bool = False,
    preserve_layout: bool = False,
) -> str:
    """Normalize user-supplied text before it reaches the database or a model.

    NUL bytes and lone surrogates are removed, line endings are unified, and
    control characters a text column or UI cannot display are dropped.
    Single-line fields collapse all whitespace. Prose fields keep paragraph
    breaks but trim noisy runs of spaces. Layout-preserving text keeps
    indentation, which matters for pasted code in chat messages.
    """
    text = sanitize_text(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARS.sub("", text)
    if single_line:
        return " ".join(text.split())
    if preserve_layout:
        lines = [line.rstrip(" \t") for line in text.split("\n")]
        return _MULTI_NEWLINE.sub("\n\n", "\n".join(lines)).strip()
    lines = []
    for line in text.split("\n"):
        line = line.rstrip(" \t")
        lines.append(_MULTI_SPACE.sub(" ", line))
    return _MULTI_NEWLINE.sub("\n\n", "\n".join(lines)).strip()


def normalize_user_text_input(
    value: Any,
    *,
    single_line: bool = False,
    preserve_layout: bool = False,
) -> Any:
    """Schema-friendly wrapper that lets Pydantic handle non-string values."""
    if not isinstance(value, str):
        return value
    return normalize_user_text(
        value, single_line=single_line, preserve_layout=preserve_layout
    )
