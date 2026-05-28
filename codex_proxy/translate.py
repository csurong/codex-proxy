"""Translation layer: Responses API <-> Chat Completions API.

Three main functions:
  req_to_chat()      — ResponsesRequest → Chat Completions request body dict
  resp_to_responses() — ChatResponse → ResponsesObject
  stream_to_sse()    — AsyncIterator[ChatStreamChunk] → AsyncGenerator[str] (SSE events)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator

from .ids import new_response_id, new_reasoning_id, new_message_id, new_function_call_id, new_call_id
from .sse import sse_event
from .types import (
    ChatResponse, ChatStreamChunk,
    ResponsesObject, ResponsesOutputItem, ResponsesRequest, ResponsesUsage,
)


# ──────────────────────────────────────────────────────────────────────
# req_to_chat: Responses API request → Chat Completions request body
# ──────────────────────────────────────────────────────────────────────

log = logging.getLogger(__name__)

# Schema for Codex's `local_shell` builtin tool, mapped to a regular function
# tool. Codex registers handlers for both `local_shell` (builtin) and `shell`
# (function), so emitting `shell` tool_calls back to it just works.
_LOCAL_SHELL_FN: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "shell",
        "description": "Execute a shell command on the local machine. Returns stdout, stderr and exit code.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": 'Argv array, e.g. ["ls", "-la"]. The first element is the program; remaining elements are arguments.',
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory to run the command in (optional).",
                },
                "timeout_ms": {
                    "type": "number",
                    "description": "Timeout in milliseconds (optional, default 30000).",
                },
            },
            "required": ["command"],
        },
    },
}

# Tools that exist server-side at OpenAI but have no equivalent at upstream providers.
_SERVER_SIDE_TOOLS = frozenset({
    "code_interpreter",
    "file_search",
    "image_generation",
    "computer_use_preview",
    "computer_use",
})

_warned_types: set[str] = set()


def _warn_once(tool_type: str, msg: str) -> None:
    if tool_type not in _warned_types:
        _warned_types.add(tool_type)
        log.warning(msg)


def _tool_to_chat(t: Any) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Convert a single Responses API tool to Chat Completions tool(s), or None to drop."""
    # Normalize to dict — accept both raw dicts and Pydantic models
    if hasattr(t, "model_dump"):
        t = t.model_dump()
    elif not isinstance(t, dict):
        t = {"type": getattr(t, "type", "function"), "name": getattr(t, "name", "")}
    tool_type = t.get("type", "function")

    # 1. Standard function tool
    if tool_type == "function":
        name = t.get("name", "")
        if not name:
            log.debug("dropping function tool with no name")
            return None
        fn: dict[str, Any] = {"name": name}
        if t.get("description"):
            fn["description"] = t["description"]
        if t.get("parameters"):
            fn["parameters"] = t["parameters"]
        # Only include strict when it's an explicit bool (not null)
        if isinstance(t.get("strict"), bool):
            fn["strict"] = t["strict"]
        return {"type": "function", "function": fn}

    # 2. local_shell → shell function tool
    if tool_type == "local_shell":
        return _LOCAL_SHELL_FN

    # 3. web_search / web_search_preview → drop (no upstream equivalent unless provider-specific)
    if tool_type in ("web_search", "web_search_preview"):
        log.debug("dropping tool type %r — no upstream equivalent", tool_type)
        return None

    # 4. custom tool → function with permissive schema
    if tool_type == "custom":
        name = t.get("name", "")
        if not name:
            log.debug("dropping custom tool with no name")
            return None
        desc = t.get("description", "")
        format_type = (t.get("format") or {}).get("type")
        if format_type:
            desc += f' (originally a "{format_type}"-format custom tool; output should follow that format).'
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": desc.strip() or None,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input": {"type": "string", "description": "Input text for the tool."},
                    },
                    "additionalProperties": True,
                },
            },
        }

    # 5. namespace wrapper → flatten nested tools
    if tool_type == "namespace":
        nested_tools = t.get("tools")
        if not isinstance(nested_tools, list) or len(nested_tools) == 0:
            log.debug("dropping namespace tool %r with no nested tools", t.get("name"))
            return None
        result: list[dict[str, Any]] = []
        for inner in nested_tools:
            r = _tool_to_chat(inner)
            if isinstance(r, list):
                result.extend(r)
            elif r is not None:
                result.append(r)
        return result if result else None

    # 6. Server-side tools — silently drop
    if tool_type in _SERVER_SIDE_TOOLS:
        log.debug("dropping server-side tool %r — no upstream equivalent", tool_type)
        return None

    # 7. mcp tools — drop (Chat Completions providers don't support MCP runtime)
    if tool_type == "mcp":
        label = t.get("server_label") or t.get("connector_id") or t.get("server_url") or "(unnamed)"
        _warn_once(f"mcp:{label}", f'mcp tool "{label}" dropped — upstream does not support MCP runtime')
        return None

    # 8. tool_search → function tool
    if tool_type == "tool_search":
        fn = {"name": "tool_search"}
        if isinstance(t.get("description"), str):
            fn["description"] = t["description"]
        if isinstance(t.get("parameters"), dict):
            fn["parameters"] = t["parameters"]
        return {"type": "function", "function": fn}

    # 9. Unknown type — drop with warning
    _warn_once(tool_type, f'dropping unknown tool type "{tool_type}"')
    return None


def req_to_chat(req: ResponsesRequest, expose_reasoning: bool = True) -> dict[str, Any]:
    """Convert a ResponsesRequest to a Chat Completions request body dict."""
    messages: list[dict[str, Any]] = []

    # 1. Instructions → system message
    if req.instructions:
        messages.append({"role": "system", "content": req.instructions})

    # 2. Process input
    if isinstance(req.input, str):
        messages.append({"role": "user", "content": req.input})
    elif isinstance(req.input, list):
        messages.extend(_process_input_list(req.input))

    # 3. Convert tools
    tools = None
    if req.tools:
        tools = []
        for t in req.tools:
            result = _tool_to_chat(t)
            if isinstance(result, list):
                tools.extend(result)
            elif result is not None:
                tools.append(result)

    # 4. Build body
    body: dict[str, Any] = {
        "model": req.model,
        "messages": messages,
        "stream": req.stream,
    }

    if tools:
        body["tools"] = tools
    if req.tool_choice != "auto":
        body["tool_choice"] = req.tool_choice
    if req.temperature is not None:
        body["temperature"] = req.temperature
    if req.top_p is not None:
        body["top_p"] = req.top_p
    if req.max_output_tokens is not None:
        body["max_completion_tokens"] = req.max_output_tokens

    # 5. Reasoning effort → reasoning_effort
    if req.reasoning and req.reasoning.effort:
        effort = req.reasoning.effort
        body["reasoning_effort"] = "low" if effort == "minimal" else effort

    return body


def _process_input_list(items: list[Any]) -> list[dict[str, Any]]:
    """Convert Responses API input array to Chat Completions messages."""
    messages: list[dict[str, Any]] = []
    pending_reasoning: str | None = None
    pending_tool_calls: list[dict[str, Any]] | None = None

    for item in items:
        if isinstance(item, str):
            messages.append({"role": "user", "content": item})
            continue
        if not isinstance(item, dict):
            continue

        role = item.get("role")
        item_type = item.get("type")

        # Reasoning item → buffer for next assistant message
        if item_type == "reasoning":
            text = ""
            # Extract from encrypted_content (preferred) or summary
            if item.get("encrypted_content"):
                text = item["encrypted_content"]
            elif item.get("summary"):
                for s in item["summary"]:
                    if isinstance(s, dict) and s.get("text"):
                        text += s["text"]
            if text:
                pending_reasoning = text
            continue

        # Function call output → tool message
        if item_type == "function_call_output":
            call_id = item.get("call_id", "")
            output = item.get("output", "")
            if isinstance(output, dict):
                output = json.dumps(output)
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": str(output),
            })
            continue

        # Function call item → buffer tool_calls
        if item_type == "function_call":
            if pending_tool_calls is None:
                pending_tool_calls = []
            pending_tool_calls.append({
                "id": item.get("call_id", new_call_id()),
                "type": "function",
                "function": {
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", ""),
                },
            })
            continue

        # Message item
        if role == "assistant":
            msg: dict[str, Any] = {"role": "assistant"}
            # Extract text content
            if isinstance(item.get("content"), list):
                texts = []
                for part in item["content"]:
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        texts.append(part.get("text", ""))
                    elif isinstance(part, dict) and part.get("type") == "text":
                        texts.append(part.get("text", ""))
                if texts:
                    msg["content"] = "\n".join(texts)
            elif isinstance(item.get("content"), str):
                msg["content"] = item["content"]

            # Attach reasoning_content if buffered
            if pending_reasoning:
                msg["reasoning_content"] = pending_reasoning
                pending_reasoning = None

            # Attach tool_calls if buffered
            if pending_tool_calls:
                msg["tool_calls"] = pending_tool_calls
                # Don't send content with tool_calls (MiMo requirement)
                msg.pop("content", None)
                pending_tool_calls = None

            if msg.get("content") or msg.get("tool_calls") or msg.get("reasoning_content"):
                messages.append(msg)
            continue

        if role in ("user", "system", "developer"):
            chat_role = "system" if role in ("system", "developer") else "user"
            content = item.get("content", "")
            if isinstance(content, list):
                parts = []
                for p in content:
                    if isinstance(p, dict):
                        if p.get("type") in ("input_text", "output_text"):
                            text = p.get("text", "")
                            if text:
                                parts.append({"type": "text", "text": text})
                        elif p.get("type") == "text":
                            parts.append(p)
                        elif p.get("type") == "input_image":
                            # Responses API → Chat Completions image_url format
                            img_url = p.get("image_url", "")
                            if img_url:
                                part: dict[str, Any] = {"type": "image_url", "image_url": {"url": img_url}}
                                if p.get("detail"):
                                    part["image_url"]["detail"] = p["detail"]
                                parts.append(part)
                        elif p.get("type") == "image_url":
                            parts.append(p)
                    elif isinstance(p, str):
                        parts.append({"type": "text", "text": p})
                if parts:
                    # MiMo requires at least one text part when images are present
                    has_image = any(p.get("type") == "image_url" for p in parts)
                    has_text = any(p.get("type") == "text" for p in parts)
                    if has_image and not has_text:
                        parts.insert(0, {"type": "text", "text": " "})
                    # Flatten text-only content into a string for cleaner upstream payloads
                    if all(p.get("type") == "text" for p in parts):
                        messages.append({"role": chat_role, "content": "".join(p["text"] for p in parts)})
                    else:
                        messages.append({"role": chat_role, "content": parts})
            elif isinstance(content, str):
                messages.append({"role": chat_role, "content": content})
            continue

        # Fallback: pass through as-is
        if role:
            messages.append(item)

    # Flush any remaining pending reasoning/tool_calls as a trailing assistant message
    if pending_tool_calls or pending_reasoning:
        msg = {"role": "assistant"}
        if pending_reasoning:
            msg["reasoning_content"] = pending_reasoning
        if pending_tool_calls:
            msg["tool_calls"] = pending_tool_calls
            msg.pop("content", None)
        messages.append(msg)

    return messages


# ──────────────────────────────────────────────────────────────────────
# resp_to_responses: Chat Completions response → ResponsesObject
# ──────────────────────────────────────────────────────────────────────


def resp_to_responses(
    chat: ChatResponse,
    req: ResponsesRequest,
    expose_reasoning: bool = True,
) -> ResponsesObject:
    """Convert a non-streaming Chat Completions response to ResponsesObject."""
    choice = chat.choices[0] if chat.choices else None
    message = choice.message if choice else None
    output: list[ResponsesOutputItem] = []

    # Reasoning content → reasoning output item
    if message and message.reasoning_content:
        output.append(ResponsesOutputItem(
            type="reasoning",
            id=new_reasoning_id(),
            status="completed",
            summary=(
                [{"type": "summary_text", "text": message.reasoning_content}]
                if expose_reasoning else []
            ),
            encrypted_content=message.reasoning_content,
        ))

    # Message content → message output item
    if message and message.content:
        output.append(ResponsesOutputItem(
            type="message",
            id=new_message_id(),
            role="assistant",
            status="completed",
            content=[{"type": "output_text", "text": message.content, "annotations": []}],
        ))

    # Tool calls → function_call output items
    if message and message.tool_calls:
        for tc in message.tool_calls:
            output.append(ResponsesOutputItem(
                type="function_call",
                id=new_function_call_id(),
                call_id=tc.get("id", new_call_id()),
                name=tc.get("function", {}).get("name", ""),
                arguments=tc.get("function", {}).get("arguments", ""),
                status="completed",
            ))

    # Usage
    usage = None
    if chat.usage:
        usage = ResponsesUsage(
            input_tokens=chat.usage.prompt_tokens,
            output_tokens=chat.usage.completion_tokens,
            total_tokens=chat.usage.total_tokens,
        )
        if chat.usage.prompt_tokens_details:
            usage.input_tokens_details = chat.usage.prompt_tokens_details
        if chat.usage.completion_tokens_details:
            usage.output_tokens_details = chat.usage.completion_tokens_details

    finish_reason = choice.finish_reason if choice else "stop"

    return ResponsesObject(
        id=new_response_id(),
        object="response",
        created_at=chat.created or int(time.time()),
        status="incomplete" if finish_reason == "length" else "completed",
        model=chat.model,
        output=output,
        usage=usage,
        reasoning={
            "effort": req.reasoning.effort if req.reasoning else None,
            "summary": req.reasoning.summary if req.reasoning else None,
        },
        max_output_tokens=req.max_output_tokens,
        temperature=req.temperature,
        top_p=req.top_p,
        tools=[dict(t) if isinstance(t, dict) else t for t in req.tools] if req.tools else [],
        incomplete_details={"reason": "max_output_tokens"} if finish_reason == "length" else None,
    )


# ──────────────────────────────────────────────────────────────────────
# stream_to_sse: Streaming Chat Completions → Responses API SSE events
# ──────────────────────────────────────────────────────────────────────


async def stream_to_sse(
    chunks: AsyncIterator[ChatStreamChunk],
    req: ResponsesRequest,
    expose_reasoning: bool = True,
) -> AsyncIterator[str]:
    """Convert streaming Chat Completions chunks to Responses API SSE event strings."""
    state = _StreamState(req.model, expose_reasoning)

    # response.created + response.in_progress
    yield sse_event("response.created", {"response": _build_snapshot(state, req, "in_progress")})
    yield sse_event("response.in_progress", {"response": _build_snapshot(state, req, "in_progress")})

    try:
        async for chunk in chunks:
            if not chunk.choices:
                # Usage-only chunk
                if chunk.usage:
                    state.usage = _map_usage(chunk.usage)
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            # Reasoning content
            if delta.reasoning_content:
                if state.active_kind != "reasoning":
                    for e in _open_reasoning(state): yield e
                state.active_buffer += delta.reasoning_content
                if state.expose_reasoning:
                    yield sse_event("response.reasoning_summary_text.delta", {
                        "item_id": state.active_item_id,
                        "output_index": state.output_index - 1,
                        "summary_index": 0,
                        "delta": delta.reasoning_content,
                    })

            # Message content
            if delta.content:
                if state.active_kind == "reasoning":
                    for e in _close_reasoning(state): yield e
                if state.active_kind != "message":
                    for e in _open_message(state): yield e
                state.active_buffer += delta.content
                yield sse_event("response.output_text.delta", {
                    "item_id": state.active_item_id,
                    "output_index": state.output_index - 1,
                    "content_index": 0,
                    "delta": delta.content,
                })

            # Annotations
            if delta.annotations:
                if state.active_kind != "message":
                    for e in _open_message(state): yield e
                for ann in delta.annotations:
                    idx = len(state.annotations)
                    state.annotations.append(ann)
                    yield sse_event("response.output_text.annotation.added", {
                        "item_id": state.active_item_id,
                        "output_index": state.output_index - 1,
                        "content_index": 0,
                        "annotation_index": idx,
                        "annotation": ann,
                    })

            # Tool calls
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    tc_idx = tc_delta.get("index", 0)
                    if tc_idx not in state.tool_calls:
                        state.tool_calls[tc_idx] = {
                            "id": tc_delta.get("id", new_call_id()),
                            "name": tc_delta.get("function", {}).get("name", ""),
                            "args_buffer": "",
                            "item_id": new_function_call_id(),
                            "output_index": state.output_index,
                        }
                        tc = state.tool_calls[tc_idx]
                        yield sse_event("response.output_item.added", {
                            "output_index": tc["output_index"],
                            "item": {
                                "id": tc["item_id"],
                                "type": "function_call",
                                "call_id": tc["id"],
                                "name": tc["name"],
                                "arguments": "",
                                "status": "in_progress",
                            },
                        })
                        state.output_index += 1

                    tc = state.tool_calls[tc_idx]
                    if tc_delta.get("function", {}).get("name") and not tc["name"]:
                        tc["name"] = tc_delta["function"]["name"]
                    if tc_delta.get("function", {}).get("arguments"):
                        tc["args_buffer"] += tc_delta["function"]["arguments"]
                        yield sse_event("response.function_call_arguments.delta", {
                            "item_id": tc["item_id"],
                            "output_index": tc["output_index"],
                            "delta": tc_delta["function"]["arguments"],
                        })

            # Finish reason
            if choice.finish_reason:
                state.finish_reason = choice.finish_reason

            # Usage (on last chunk)
            if chunk.usage:
                state.usage = _map_usage(chunk.usage)

    except Exception as e:
        # Error: emit response.failed
        for e in _finalize_all(state): yield e
        snapshot = _build_snapshot(state, req, "failed")
        snapshot["error"] = {"type": "upstream_error", "message": str(e)}
        yield sse_event("response.failed", {"response": snapshot})
        return

    # Normal completion
    for e in _finalize_all(state): yield e
    yield sse_event("response.completed", {"response": _build_snapshot(state, req, "completed")})


# ── Stream state machine ──


class _StreamState:
    def __init__(self, model: str, expose_reasoning: bool):
        self.response_id = new_response_id()
        self.created_at = int(time.time())
        self.model = model
        self.expose_reasoning = expose_reasoning
        self.output_index = 0
        self.sequence_number = 0
        self.active_kind: str | None = None  # "reasoning" | "message"
        self.active_item_id: str | None = None
        self.active_buffer = ""
        self.annotations: list[Any] = []
        self.tool_calls: dict[int, dict[str, Any]] = {}
        self.finish_reason: str | None = None
        self.usage: ResponsesUsage | None = None

    def next_seq(self) -> int:
        s = self.sequence_number
        self.sequence_number += 1
        return s


def _open_reasoning(state: _StreamState):
    """Open a reasoning output item. Yields SSE events."""
    if state.active_kind:
        for e in _finalize_active(state): yield e
    state.active_kind = "reasoning"
    state.active_item_id = new_reasoning_id()
    state.active_buffer = ""
    idx = state.output_index
    state.output_index += 1

    yield sse_event("response.output_item.added", {
        "output_index": idx,
        "item": {
            "id": state.active_item_id,
            "type": "reasoning",
            "summary": [],
            "encrypted_content": None,
            "status": "in_progress",
        },
    })
    if state.expose_reasoning:
        yield sse_event("response.reasoning_summary_part.added", {
            "item_id": state.active_item_id,
            "output_index": idx,
            "summary_index": 0,
            "part": {"type": "summary_text", "text": ""},
        })


def _close_reasoning(state: _StreamState):
    """Close the reasoning item. Yields SSE events."""
    if state.active_kind != "reasoning":
        return
    buf = state.active_buffer
    idx = state.output_index - 1

    if state.expose_reasoning:
        yield sse_event("response.reasoning_summary_text.done", {
            "item_id": state.active_item_id,
            "output_index": idx,
            "summary_index": 0,
        })
        yield sse_event("response.reasoning_summary_part.done", {
            "item_id": state.active_item_id,
            "output_index": idx,
            "summary_index": 0,
            "part": {"type": "summary_text", "text": buf},
        })

    yield sse_event("response.output_item.done", {
        "output_index": idx,
        "item": {
            "id": state.active_item_id,
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": buf}] if state.expose_reasoning else [],
            "encrypted_content": buf,
            "status": "completed",
        },
    })
    state.active_kind = None
    state.active_item_id = None
    state.active_buffer = ""


def _open_message(state: _StreamState):
    """Open a message output item."""
    if state.active_kind:
        for e in _finalize_active(state): yield e
    state.active_kind = "message"
    state.active_item_id = new_message_id()
    state.active_buffer = ""
    state.annotations = []
    idx = state.output_index
    state.output_index += 1

    yield sse_event("response.output_item.added", {
        "output_index": idx,
        "item": {
            "id": state.active_item_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "status": "in_progress",
        },
    })


def _close_message(state: _StreamState):
    """Close the message item."""
    if state.active_kind != "message":
        return
    idx = state.output_index - 1
    yield sse_event("response.output_text.done", {
        "item_id": state.active_item_id,
        "output_index": idx,
        "content_index": 0,
        "text": state.active_buffer,
    })
    yield sse_event("response.output_item.done", {
        "output_index": idx,
        "item": {
            "id": state.active_item_id,
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": state.active_buffer, "annotations": state.annotations}],
        },
    })
    state.active_kind = None
    state.active_item_id = None
    state.active_buffer = ""


def _finalize_active(state: _StreamState):
    """Finalize whatever item is currently active."""
    if state.active_kind == "reasoning":
        for e in _close_reasoning(state): yield e
    elif state.active_kind == "message":
        for e in _close_message(state): yield e


def _finalize_tool_calls(state: _StreamState):
    """Finalize all open tool calls."""
    for tc in state.tool_calls.values():
        yield sse_event("response.function_call_arguments.done", {
            "item_id": tc["item_id"],
            "output_index": tc["output_index"],
        })
        yield sse_event("response.output_item.done", {
            "output_index": tc["output_index"],
            "item": {
                "id": tc["item_id"],
                "type": "function_call",
                "call_id": tc["id"],
                "name": tc["name"],
                "arguments": tc["args_buffer"],
                "status": "completed",
            },
        })


def _finalize_all(state: _StreamState):
    """Finalize everything: active item + tool calls."""
    for e in _finalize_active(state): yield e
    for e in _finalize_tool_calls(state): yield e


def _build_snapshot(state: _StreamState, req: ResponsesRequest, status: str) -> dict[str, Any]:
    """Build a ResponsesObject snapshot for SSE events."""
    incomplete = None
    if state.finish_reason == "length":
        incomplete = {"reason": "max_output_tokens"}

    return {
        "id": state.response_id,
        "object": "response",
        "created_at": state.created_at,
        "status": status,
        "model": state.model,
        "output": [],  # Not full — events carry items individually
        "usage": state.usage.model_dump() if state.usage else None,
        "parallel_tool_calls": req.parallel_tool_calls,
        "tool_choice": req.tool_choice,
        "reasoning": {
            "effort": req.reasoning.effort if req.reasoning else None,
            "summary": req.reasoning.summary if req.reasoning else None,
        },
        "text": req.text or {"format": {"type": "text"}},
        "incomplete_details": incomplete,
        "error": None,
        "metadata": req.metadata,
        "previous_response_id": req.previous_response_id,
        "instructions": req.instructions,
        "temperature": req.temperature,
        "top_p": req.top_p,
        "max_output_tokens": req.max_output_tokens,
        "tools": [dict(t) if isinstance(t, dict) else t for t in req.tools] if req.tools else [],
        "truncation": "disabled",
    }


def _map_usage(u: Any) -> ResponsesUsage:
    """Map ChatUsage to ResponsesUsage."""
    ru = ResponsesUsage(
        input_tokens=u.prompt_tokens,
        output_tokens=u.completion_tokens,
        total_tokens=u.total_tokens,
    )
    if u.prompt_tokens_details:
        ru.input_tokens_details = u.prompt_tokens_details
    if u.completion_tokens_details:
        ru.output_tokens_details = u.completion_tokens_details
    return ru
