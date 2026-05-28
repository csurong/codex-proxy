"""Upstream HTTP client for Chat Completions API."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from .types import ChatResponse, ChatStreamChunk, ChatUsage


class UpstreamError(Exception):
    def __init__(self, status_code: int, message: str, body: str = ""):
        self.status_code = status_code
        self.message = message
        self.body = body
        super().__init__(f"Upstream {status_code}: {message}")


async def call_upstream(
    base_url: str,
    api_key: str | None,
    body: dict[str, Any],
    timeout: float = 120.0,
) -> ChatResponse:
    """Non-streaming upstream call."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url}/chat/completions", json=body, headers=headers)

    if resp.status_code >= 400:
        raise UpstreamError(resp.status_code, resp.text[:500], resp.text)

    data = resp.json()
    return _parse_chat_response(data)


async def call_upstream_stream(
    base_url: str,
    api_key: str | None,
    body: dict[str, Any],
    timeout: float = 120.0,
) -> AsyncIterator[ChatStreamChunk]:
    """Streaming upstream call — yields ChatStreamChunk per SSE data line."""
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

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
