"""Provider abstraction: MiMo, vLLM, Custom normalization."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelMeta:
    model_id: str
    display_name: str = ""
    supports_images: bool = False
    supports_reasoning: bool = False
    supports_tools: bool = False
    context_window: int | None = None
    max_output_tokens: int | None = None


@dataclass
class ProviderRuntime:
    id: str
    type: str  # "mimo" | "vllm" | "custom"
    display_name: str
    base_url: str
    api_key: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    models: list[ModelMeta] = field(default_factory=list)

    @classmethod
    def from_db_row(cls, row: dict[str, Any], models: list[dict[str, Any]] | None = None) -> ProviderRuntime:
        cfg = {}
        if row.get("config_json"):
            try:
                cfg = json.loads(row["config_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        mms = []
        for m in (models or []):
            mms.append(ModelMeta(
                model_id=m["model_id"],
                display_name=m.get("display_name", ""),
                supports_images=bool(m.get("supports_images")),
                supports_reasoning=bool(m.get("supports_reasoning")),
                supports_tools=bool(m.get("supports_tools")),
                context_window=m.get("context_window"),
                max_output_tokens=m.get("max_output_tokens"),
            ))
        return cls(
            id=row["id"],
            type=row["type"],
            display_name=row["display_name"],
            base_url=row.get("base_url", ""),
            api_key=row.get("api_key"),
            config=cfg,
            models=mms,
        )


# ── Provider-specific normalization ──


MIMO_THINKING_DISABLED_MODELS = {"mimo-v2-flash"}

MIMO_PAYG_URL = "https://api.xiaomimimo.com/v1"
MIMO_TOKEN_PLAN_URL = "https://token-plan-cn.xiaomimimo.com/v1"


def resolve_mimo_base_url(api_key: str | None, configured_url: str) -> str:
    """Auto-detect token-plan vs pay-as-you-go base URL by key prefix."""
    if not api_key:
        return configured_url
    if api_key.startswith("tp-"):
        return MIMO_TOKEN_PLAN_URL
    return MIMO_PAYG_URL


def normalize_mimo(body: dict[str, Any], model_id: str, thinking_on: bool | None) -> dict[str, Any]:
    """Apply MiMo-specific normalization to Chat Completions request body."""
    if thinking_on is None:
        thinking_on = model_id not in MIMO_THINKING_DISABLED_MODELS

    if thinking_on:
        body["thinking"] = {"type": "enabled"}
        # MiMo forces temperature=1.0 in thinking mode; strip user value
        body.pop("temperature", None)
    else:
        body["thinking"] = {"type": "disabled"}

    # MiMo uses max_completion_tokens, not max_tokens
    if "max_tokens" in body:
        body["max_completion_tokens"] = body.pop("max_tokens")

    return body


def normalize_vllm(body: dict[str, Any], config: dict[str, Any], thinking_on: bool | None) -> dict[str, Any]:
    """Apply vLLM/Qwen-specific normalization."""
    if thinking_on is None:
        thinking_on = config.get("enable_thinking", False)

    if thinking_on:
        body.setdefault("chat_template_kwargs", {})
        body["chat_template_kwargs"]["enable_thinking"] = True

    return body


def normalize_custom(body: dict[str, Any]) -> dict[str, Any]:
    """Custom provider: passthrough, no modifications."""
    return body


def normalize_body(
    runtime: ProviderRuntime,
    body: dict[str, Any],
    model_id: str,
    thinking_on: bool | None = None,
) -> dict[str, Any]:
    """Dispatch to provider-specific normalization."""
    if runtime.type == "mimo":
        return normalize_mimo(body, model_id, thinking_on)
    elif runtime.type == "vllm":
        return normalize_vllm(body, runtime.config, thinking_on)
    else:
        return normalize_custom(body)


# ── Provider selection ──


def find_provider_for_model(
    providers: list[ProviderRuntime],
    model_id: str,
) -> tuple[ProviderRuntime, ModelMeta | None]:
    """Find the provider and model metadata for a given model ID.

    Checks in order:
    1. Exact match in any provider's model list
    2. Provider ID prefix (e.g., "mimo/mimo-v2.5-pro" → provider "mimo")
    3. First provider (fallback)
    """
    # 1. Exact model match (only enabled models)
    for p in providers:
        for m in p.models:
            if m.model_id == model_id:
                return p, m

    # 2. Provider prefix
    if "/" in model_id:
        prefix = model_id.split("/", 1)[0]
        for p in providers:
            if p.id == prefix:
                return p, None

    # 3. Fallback to first provider
    if providers:
        return providers[0], None

    raise ValueError(f"No provider found for model '{model_id}'")


def strip_images_for_non_vision(
    messages: list[dict[str, Any]], model_meta: ModelMeta | None
) -> list[dict[str, Any]]:
    """Remove image_url content parts for non-vision models."""
    if model_meta and model_meta.supports_images:
        return messages

    cleaned = []
    for msg in messages:
        if isinstance(msg.get("content"), list):
            new_parts = []
            for part in msg["content"]:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    continue  # strip
                new_parts.append(part)
            if not new_parts:
                new_parts = [{"type": "text", "text": " "}]
            msg = {**msg, "content": new_parts}
        cleaned.append(msg)
    return cleaned
