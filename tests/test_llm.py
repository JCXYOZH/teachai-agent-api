from typing import Any, Dict

import pytest

from app.services.llm import LLMProviderError, call_openai_compatible


class FakeResult:
    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self.content = content
        self.response_metadata = {"finish_reason": finish_reason}


class FakeChat:
    captured: Dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        FakeChat.captured = kwargs

    async def ainvoke(self, _messages: Any) -> FakeResult:
        return FakeResult('{"ok": true}')


async def test_openai_compatible_disables_thinking(monkeypatch: Any) -> None:
    monkeypatch.setattr("app.services.llm.ChatOpenAI", FakeChat)

    text = await call_openai_compatible(
        base_url="http://127.0.0.1:65535/v1",
        api_key=None,
        model="Qwen3.6-35B-A3B",
        system_prompt="system",
        user_prompt="user",
        timeout=5,
        disable_thinking=True,
    )

    assert text == '{"ok": true}'
    assert FakeChat.captured["model_kwargs"]["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }


async def test_openai_compatible_merges_text_only_and_thinking(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr("app.services.llm.ChatOpenAI", FakeChat)

    await call_openai_compatible(
        base_url="https://example.test/v1",
        api_key="test-key",
        model="qwen3.8-omni-flash",
        system_prompt="system",
        user_prompt="user",
        timeout=5,
        text_only=True,
        disable_thinking=True,
    )

    assert FakeChat.captured["model_kwargs"]["extra_body"] == {
        "modalities": ["text"],
        "chat_template_kwargs": {"enable_thinking": False},
    }


async def test_openai_compatible_rejects_empty_content(monkeypatch: Any) -> None:
    class EmptyChat(FakeChat):
        async def ainvoke(self, _messages: Any) -> FakeResult:
            return FakeResult("", "length")

    monkeypatch.setattr("app.services.llm.ChatOpenAI", EmptyChat)

    with pytest.raises(LLMProviderError, match="finish_reason=length"):
        await call_openai_compatible(
            base_url="http://127.0.0.1:65535/v1",
            api_key=None,
            model="Qwen3.6-35B-A3B",
            system_prompt="system",
            user_prompt="user",
            timeout=5,
        )
