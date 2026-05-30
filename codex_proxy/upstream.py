"""Upstream HTTP client for Chat Completions API."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator

import httpx

from .types import ChatResponse, ChatStreamChunk, ChatUsage

log = logging.getLogger(__name__)

_CONTEXT_OVERFLOW_PATTERNS = [
    re.compile(r"maximum context length", re.I),
    re.compile(r"context length exceeded", re.I),
    re.compile(r"too many tokens", re.I),
    re.compile(r"token limit", re.I),
    re.compile(r"request too large", re.I),
    re.compile(r"max.*context", re.I),
    re.compile(r"上下文.*超", re.I),
    re.compile(r"输入.*过长", re.I),
    re.compile(r"token.*超", re.I),
]

_CONTEXT_OVERFLOW_HINT = (
    "Context window exceeded. The conversation is too long for this model. "
    "Try running /compact to summarize and shorten the conversation, or start a new session."
)

_WEB_SEARCH_DISABLED_MARKER = "webSearchEnabled is false"
_WEB_SEARCH_DISABLED_HINT = (
    "MiMo Web Search Plugin is not activated for this account. "
    "Activate it at https://platform.xiaomimimo.com/#/console/plugin, then retry. "
    "If this is a token-plan account, web_search is not forwarded by codex-proxy."
)


def _is_context_overflow(body: str) -> bool:
    """Check if an error body looks like a context-window overflow."""
    return any(p.search(body) for p in _CONTEXT_OVERFLOW_PATTERNS)


class UpstreamError(Exception):
    def __init__(self, status_code: int, message: str, body: str = ""):
        self.status_code = status_code
        self.message = message
        self.body = body
        # Enhance context overflow errors
        if status_code == 400 and _is_context_overflow(body):
            self.message = _CONTEXT_OVERFLOW_HINT
        elif status_code == 400 and _WEB_SEARCH_DISABLED_MARKER in body:
            self.message = _WEB_SEARCH_DISABLED_HINT
        super().__init__(f"Upstream {status_code}: {self.message}")


async def call_upstream(
    base_url: str,
    api_key: str | None,
    body: dict[str, Any],
    timeout: float = 120.0,
    retries: int = 1,
) -> ChatResponse:
    """Non-streaming upstream call with retry on connection failure."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    last_exc: Exception | None = None
    for attempt in range(1 + retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{base_url}/chat/completions", json=body, headers=headers)

            if resp.status_code >= 400:
                raise UpstreamError(resp.status_code, resp.text[:500], resp.text)

            data = resp.json()
            return _parse_chat_response(data)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as e:
            last_exc = e
            if attempt < retries:
                log.warning("Upstream connection failed (attempt %d/%d), retrying: %s", attempt + 1, 1 + retries, e)
                continue
            raise UpstreamError(502, f"Connection failed after {1 + retries} attempts: {e}") from e
        except UpstreamError:
            raise

    raise last_exc  # type: ignore[misc]


async def call_upstream_stream(
    base_url: str,
    api_key: str | None,
    body: dict[str, Any],
    timeout: float = 120.0,
    retries: int = 1,
) -> AsyncIterator[ChatStreamChunk]:
    """Streaming upstream call — yields ChatStreamChunk per SSE data line."""
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Retry only on connection errors (before any data flows)
    last_exc: Exception | None = None
    for attempt in range(1 + retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{base_url}/chat/completions", json=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        body_text = ""
                        async for chunk in resp.aiter_text():
                            body_text += chunk
                        raise UpstreamError(resp.status_code, body_text[:500], body_text)

                    buffer = ""
                    async for raw in resp.aiter_text():
                        buffer += raw
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line or line.startswith(":"):
                                continue
                            if line.startswith("data: "):
                                data_str = line[6:]
                                if data_str.strip() == "[DONE]":
                                    return
                                try:
                                    data = json.loads(data_str)
                                    yield _parse_stream_chunk(data)
                                except json.JSONDecodeError:
                                    continue
                    return
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as e:
            last_exc = e
            if attempt < retries:
                log.warning("Upstream stream connection failed (attempt %d/%d), retrying: %s", attempt + 1, 1 + retries, e)
                continue
            raise UpstreamError(502, f"Connection failed after {1 + retries} attempts: {e}") from e
        except UpstreamError:
            raise

    if last_exc:
        raise UpstreamError(502, f"Connection failed: {last_exc}") from last_exc


def _parse_chat_response(data: dict[str, Any]) -> ChatResponse:
    usage = None
    if data.get("usage"):
        u = data["usage"]
        usage = ChatUsage(
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            total_tokens=u.get("total_tokens", 0),
            prompt_tokens_details=u.get("prompt_tokens_details"),
            completion_tokens_details=u.get("completion_tokens_details"),
        )
    return ChatResponse(
        id=data.get("id", ""),
        object=data.get("object", "chat.completion"),
        created=data.get("created", 0),
        model=data.get("model", ""),
        choices=[{
            "index": c.get("index", 0),
            "message": c.get("message", {"role": "assistant"}),
            "finish_reason": c.get("finish_reason"),
        } for c in data.get("choices", [])],
        usage=usage,
    )


def _parse_stream_chunk(data: dict[str, Any]) -> ChatStreamChunk:
    usage = None
    if data.get("usage"):
        u = data["usage"]
        usage = ChatUsage(
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            total_tokens=u.get("total_tokens", 0),
            prompt_tokens_details=u.get("prompt_tokens_details"),
            completion_tokens_details=u.get("completion_tokens_details"),
        )
    return ChatStreamChunk(
        id=data.get("id", ""),
        object=data.get("object", "chat.completion.chunk"),
        created=data.get("created", 0),
        model=data.get("model", ""),
        choices=[{
            "index": c.get("index", 0),
            "delta": c.get("delta", {}),
            "finish_reason": c.get("finish_reason"),
        } for c in data.get("choices", [])],
        usage=usage,
    )
