from typing import Any


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
