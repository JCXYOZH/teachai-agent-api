import logging
from typing import Any, Dict, List, Optional

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic.v1 import SecretStr

logger = logging.getLogger(__name__)


class LLMTimeoutError(RuntimeError):
    pass


class LLMProviderError(RuntimeError):
    pass


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return str(content)


async def call_openai_compatible(
    *,
    base_url: str,
    api_key: Optional[str],
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout: float,
    max_tokens: int = 4096,
    temperature: float = 0.4,
    text_only: bool = False,
    disable_thinking: bool = False,
) -> str:
    model_kwargs: Dict[str, Any] = {}
    extra_body: Dict[str, Any] = {}
    if text_only:
        extra_body["modalities"] = ["text"]
    if disable_thinking:
        extra_body["chat_template_kwargs"] = {"enable_thinking": False}
    if extra_body:
        model_kwargs["extra_body"] = extra_body
    async_client = httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=False,
        trust_env=False,
    )
    try:
        chat = ChatOpenAI(
            model=model,
            api_key=SecretStr(api_key or "local-no-key"),
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=0,
            http_async_client=async_client,
            model_kwargs=model_kwargs,
        )
        result = await chat.ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
        )
        text = message_text(result.content)
        if not text.strip():
            finish_reason = result.response_metadata.get("finish_reason")
            logger.warning(
                "llm_empty_content model=%s finish_reason=%s", model, finish_reason
            )
            raise LLMProviderError(
                f"Model returned no content (finish_reason={finish_reason})"
            )
        return text
    except LLMProviderError:
        raise
    except Exception as exc:
        logger.warning(
            "llm_openai_failed model=%s error=%s",
            model,
            type(exc).__name__,
        )
        if "timeout" in type(exc).__name__.lower():
            raise LLMTimeoutError("Model request timed out") from exc
        raise LLMProviderError("Model provider request failed") from exc
    finally:
        await async_client.aclose()


async def call_anthropic(
    *,
    base_url: str,
    api_key: Optional[str],
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout: float,
    max_tokens: int = 4096,
    temperature: float = 0.4,
) -> str:
    headers = {
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    if api_key:
        headers["x-api-key"] = api_key
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(
                f"{base_url}/messages",
                headers=headers,
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            )
    except httpx.TimeoutException as exc:
        raise LLMTimeoutError("Model request timed out") from exc
    except httpx.HTTPError as exc:
        raise LLMProviderError("Model provider could not be reached") from exc

    if response.status_code != 200:
        logger.warning(
            "llm_anthropic_failed status=%s model=%s",
            response.status_code,
            model,
        )
        if response.status_code in {401, 403}:
            raise LLMProviderError("Model provider rejected the configured credentials")
        raise LLMProviderError("Model provider request failed")
    try:
        payload = response.json()
        blocks = payload.get("content", []) if isinstance(payload, dict) else []
        text = "".join(str(block.get("text", "")) for block in blocks if isinstance(block, dict))
    except ValueError as exc:
        raise LLMProviderError("Model provider returned invalid JSON") from exc
    if not text.strip():
        raise LLMProviderError("Model provider returned an empty response")
    return text
