"""Pydantic models for Responses API and Chat Completions API."""

from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


# ── Responses API (Codex-facing) ──


class ResponsesTool(BaseModel):
    type: str = "function"
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    strict: bool | None = None


class ResponsesReasoning(BaseModel):
    effort: str | None = None
    summary: str | None = None


class ResponsesRequest(BaseModel):
    model: str
    input: str | list[Any]
    stream: bool = False
    tools: list[Any] = Field(default_factory=list)
    tool_choice: str | dict[str, Any] = "auto"
    reasoning: ResponsesReasoning | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    instructions: str | None = None
    metadata: dict[str, Any] | None = None
    previous_response_id: str | None = None
    parallel_tool_calls: bool = True
    text: dict[str, Any] | None = None
    truncation: str = "disabled"


class ResponsesOutputItem(BaseModel):
    type: str  # "reasoning" | "message" | "function_call"
    id: str = ""
    status: str = "completed"
    role: str | None = None
    content: list[dict[str, Any]] | None = None
    summary: list[dict[str, Any]] = Field(default_factory=list)
    encrypted_content: str | None = None
    call_id: str | None = None
    name: str | None = None
    arguments: str | None = None


class ResponsesUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_tokens_details: dict[str, Any] | None = None
    output_tokens_details: dict[str, Any] | None = None


class ResponsesObject(BaseModel):
    id: str = ""
    object: str = "response"
    created_at: int = 0
    status: str = "completed"
    model: str = ""
    output: list[ResponsesOutputItem] = Field(default_factory=list)
    usage: ResponsesUsage | None = None
    parallel_tool_calls: bool = True
    tool_choice: str | dict[str, Any] = "auto"
    reasoning: dict[str, Any] | None = None
    text: dict[str, Any] | None = None
    incomplete_details: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    previous_response_id: str | None = None
    instructions: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_output_tokens: int | None = None
    tools: list[Any] = Field(default_factory=list)
    truncation: str = "disabled"


# ── Chat Completions API (upstream) ──


class ChatRequest(BaseModel):
    model: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_completion_tokens: int | None = None
    reasoning_effort: str | None = None
    thinking: dict[str, Any] | None = None
    enable_thinking: bool | None = None


class ChatChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str | None = None
    tool_calls: list[Any] | None = None
    reasoning_content: str | None = None


class ChatChoice(BaseModel):
    index: int = 0
    message: ChatChoiceMessage = Field(default_factory=ChatChoiceMessage)
    finish_reason: str | None = None


class ChatUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_tokens_details: dict[str, Any] | None = None
    completion_tokens_details: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    id: str = ""
    object: str = "chat.completion"
    created: int = 0
    model: str = ""
    choices: list[ChatChoice] = Field(default_factory=list)
    usage: ChatUsage | None = None


class ChatStreamDelta(BaseModel):
    role: str | None = None
    content: str | None = None
    tool_calls: list[Any] | None = None
    reasoning_content: str | None = None
    annotations: list[Any] | None = None


class ChatStreamChoice(BaseModel):
    index: int = 0
    delta: ChatStreamDelta = Field(default_factory=ChatStreamDelta)
    finish_reason: str | None = None


class ChatStreamChunk(BaseModel):
    id: str = ""
    object: str = "chat.completion.chunk"
    created: int = 0
    model: str = ""
    choices: list[ChatStreamChoice] = Field(default_factory=list)
    usage: ChatUsage | None = None
