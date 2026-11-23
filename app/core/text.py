import re
from typing import Any

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_MULTI_SPACE = re.compile(r"[^\S\n]{3,}")
_MULTI_NEWLINE = re.compile(r"\n{3,}")


def sanitize_text(value: str) -> str:
    """移除 PostgreSQL 文本列无法存储的字符。

    NUL 字节会让 asyncpg 抛出 CharacterNotInRepertoireError，孤立代理项
    则根本无法编码为 UTF-8。
    """
    if "\x00" in value:
        value = value.replace("\x00", "")
    return value.encode("utf-8", "ignore").decode("utf-8")


def sanitize_json(value: Any) -> Any:
    """递归清洗类 JSON 载荷中的每一个字符串。"""
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
    """在用户文本写入数据库或送入模型之前做规范化处理。

    移除 NUL 字节与孤立代理项、统一换行符，并丢弃文本列或界面无法展示的
    控制字符。单行字段压缩全部空白；正文保留段落但收敛连续空格；保留版式
    的文本维持缩进，以便聊天消息中粘贴的代码保持原样。
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
    """便于 Schema 使用的包装器，非字符串取值交给 Pydantic 自行处理。"""
    if not isinstance(value, str):
        return value
    return normalize_user_text(
        value, single_line=single_line, preserve_layout=preserve_layout
    )
