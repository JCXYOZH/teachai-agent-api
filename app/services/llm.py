import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic.v1 import SecretStr

from app.core.metrics import record_llm_call

logger = logging.getLogger(__name__)


class LLMTimeoutError(RuntimeError):
    pass


class LLMProviderError(RuntimeError):
    pass


class LLMEmptyResponseError(LLMProviderError):
    pass


def _finish_call(
    provider_id: Optional[str], model: str, outcome: str, started: float
) -> None:
    duration_ms = (time.perf_counter() - started) * 1000
    record_llm_call(provider_id, model, outcome, duration_ms)


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
    provider_id: Optional[str] = None,
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
    started = time.perf_counter()
    outcome = "error"
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
            outcome = "empty"
            raise LLMEmptyResponseError(
                f"Model returned no content (finish_reason={finish_reason})"
            )
        outcome = "ok"
        return text
    except LLMProviderError:
        raise
    except Exception as exc:
        is_timeout = "timeout" in type(exc).__name__.lower()
        outcome = "timeout" if is_timeout else "error"
        logger.warning(
            "llm_openai_failed provider=%s model=%s error=%s",
            provider_id or "unknown",
            model,
            type(exc).__name__,
        )
        if is_timeout:
            raise LLMTimeoutError("Model request timed out") from exc
        raise LLMProviderError("Model provider request failed") from exc
    finally:
        _finish_call(provider_id, model, outcome, started)
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
    provider_id: Optional[str] = None,
) -> str:
    headers = {
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    if api_key:
        headers["x-api-key"] = api_key
    started = time.perf_counter()
    outcome = "error"
    try:
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
            outcome = "timeout"
            raise LLMTimeoutError("Model request timed out") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError("Model provider could not be reached") from exc

        if response.status_code != 200:
            logger.warning(
                "llm_anthropic_failed provider=%s status=%s model=%s",
                provider_id or "unknown",
                response.status_code,
                model,
            )
            if response.status_code in {401, 403}:
                raise LLMProviderError(
                    "Model provider rejected the configured credentials"
                )
            raise LLMProviderError("Model provider request failed")
        try:
            payload = response.json()
            blocks = payload.get("content", []) if isinstance(payload, dict) else []
            text = "".join(
                str(block.get("text", ""))
                for block in blocks
                if isinstance(block, dict)
            )
        except ValueError as exc:
            raise LLMProviderError("Model provider returned invalid JSON") from exc
        if not text.strip():
            outcome = "empty"
            raise LLMEmptyResponseError("Model provider returned an empty response")
        outcome = "ok"
        return text
    finally:
        _finish_call(provider_id, model, outcome, started)


async def stream_openai_compatible(
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
    provider_id: Optional[str] = None,
) -> AsyncIterator[str]:
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
    started = time.perf_counter()
    outcome = "cancelled"
    saw_text = False
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
        async for chunk in chat.astream(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
        ):
            text = message_text(chunk.content)
            if not text:
                continue
            saw_text = True
            yield text
        if not saw_text:
            outcome = "empty"
            raise LLMEmptyResponseError("Model returned no content")
        outcome = "ok"
    except LLMProviderError:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        is_timeout = "timeout" in type(exc).__name__.lower()
        outcome = "timeout" if is_timeout else "error"
        logger.warning(
            "llm_openai_stream_failed provider=%s model=%s error=%s",
            provider_id or "unknown",
            model,
            type(exc).__name__,
        )
        if is_timeout:
            raise LLMTimeoutError("Model request timed out") from exc
        raise LLMProviderError("Model provider request failed") from exc
    finally:
        _finish_call(provider_id, model, outcome, started)
        await async_client.aclose()


async def stream_anthropic(
    *,
    base_url: str,
    api_key: Optional[str],
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout: float,
    max_tokens: int = 4096,
    temperature: float = 0.4,
    provider_id: Optional[str] = None,
) -> AsyncIterator[str]:
    headers = {
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if api_key:
        headers["x-api-key"] = api_key
    started = time.perf_counter()
    outcome = "cancelled"
    saw_text = False
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream(
                "POST",
                f"{base_url}/messages",
                headers=headers,
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                    "stream": True,
                },
            ) as response:
                if response.status_code != 200:
                    outcome = "error"
                    logger.warning(
                        "llm_anthropic_stream_failed provider=%s status=%s model=%s",
                        provider_id or "unknown",
                        response.status_code,
                        model,
                    )
                    if response.status_code in {401, 403}:
                        raise LLMProviderError(
                            "Model provider rejected the configured credentials"
                        )
                    raise LLMProviderError("Model provider request failed")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        event = json.loads(raw)
                    except ValueError as exc:
                        outcome = "error"
                        raise LLMProviderError(
                            "Model provider returned invalid JSON"
                        ) from exc
                    if not isinstance(event, dict):
                        continue
                    event_type = event.get("type")
                    if event_type == "error":
                        outcome = "error"
                        raise LLMProviderError("Model provider request failed")
                    if event_type == "message_stop":
                        break
                    if event_type != "content_block_delta":
                        continue
                    delta = event.get("delta")
                    if not isinstance(delta, dict):
                        continue
                    text = delta.get("text")
                    if isinstance(text, str) and text:
                        saw_text = True
                        yield text
        if not saw_text:
            outcome = "empty"
            raise LLMEmptyResponseError("Model provider returned an empty response")
        outcome = "ok"
    except LLMProviderError:
        raise
    except httpx.TimeoutException as exc:
        outcome = "timeout"
        raise LLMTimeoutError("Model request timed out") from exc
    except httpx.HTTPError as exc:
        outcome = "error"
        raise LLMProviderError("Model provider could not be reached") from exc
    finally:
        _finish_call(provider_id, model, outcome, started)
