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


@dataclass
class ProviderSelection:
    provider: ProviderRuntime
    model_id: str
    request_model: str
    model_meta: ModelMeta | None = None
    rewritten: bool = False
    reason: str | None = None


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


def is_mimo_token_plan(api_key: str | None, base_url: str) -> bool:
    """Return whether a MiMo runtime is using the token-plan host."""
    return bool((api_key or "").startswith("tp-") or "token-plan" in (base_url or "").lower())


def normalize_mimo(body: dict[str, Any], model_id: str, thinking_on: bool | None) -> dict[str, Any]:
    """Apply MiMo-specific normalization to Chat Completions request body."""
    if thinking_on is None:
        thinking = body.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "disabled":
            thinking_on = False
        elif isinstance(thinking, dict) and thinking.get("type") == "enabled":
            thinking_on = True
        else:
            thinking_on = model_id not in MIMO_THINKING_DISABLED_MODELS

    if thinking_on:
        body["thinking"] = {"type": "enabled"}
        # MiMo forces temperature=1.0 in thinking mode; strip user value
        body.pop("temperature", None)
        body.pop("top_p", None)
        # Strip tool_choice when not "auto" — MiMo rejects non-auto in thinking mode
        if body.get("tool_choice") and body["tool_choice"] != "auto":
            body.pop("tool_choice", None)
    else:
        body["thinking"] = {"type": "disabled"}
        # Strip reasoning_effort when thinking is disabled
        body.pop("reasoning_effort", None)

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


def body_has_images(body: dict[str, Any]) -> bool:
    """Return whether a Chat Completions body contains image_url content."""
    for message in body.get("messages", []):
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                return True
    return False


def _content_has_images(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(part, dict) and part.get("type") == "image_url" for part in content)


def latest_user_message_has_images(body: dict[str, Any]) -> bool:
    """Return whether the latest user message contains image_url content."""
    for message in reversed(body.get("messages", [])):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        return _content_has_images(message.get("content"))
    return False


def find_image_model(provider: ProviderRuntime) -> ModelMeta | None:
    """Pick the first image-capable model declared for this provider."""
    return next((m for m in provider.models if m.supports_images), None)


# ── Provider selection ──


def _provider_priority(providers: list[ProviderRuntime]) -> list[ProviderRuntime]:
    """Prefer user-configured local/custom providers over built-in MiMo models."""
    return [p for p in providers if p.type != "mimo"] + [p for p in providers if p.type == "mimo"]


def _find_model(provider: ProviderRuntime, model_id: str) -> ModelMeta | None:
    return next((m for m in provider.models if m.model_id == model_id), None)


def _aliases(provider: ProviderRuntime) -> dict[str, str]:
    aliases = provider.config.get("aliases")
    if not isinstance(aliases, dict):
        return {}
    return {str(k): str(v) for k, v in aliases.items() if k and v}


def resolve_provider_for_model(
    providers: list[ProviderRuntime],
    model_id: str,
) -> ProviderSelection:
    """Resolve a client model name to the provider and upstream model to call.

    Resolution order:
    1. Provider aliases, preferring user-configured providers over built-in MiMo.
    2. Exact model match, with the same provider priority.
    3. Provider ID prefix (for example, "vllm/qwq-32b" -> provider "vllm").
    4. First provider with a declared model, rewriting unknown model names to it.
    5. First provider with the original model when no model catalog exists.
    """
    ordered = _provider_priority(providers)

    for provider in ordered:
        alias_target = _aliases(provider).get(model_id)
        if alias_target:
            return ProviderSelection(
                provider=provider,
                model_id=alias_target,
                request_model=model_id,
                model_meta=_find_model(provider, alias_target),
                rewritten=alias_target != model_id,
                reason="alias",
            )

    for provider in ordered:
        meta = _find_model(provider, model_id)
        if meta:
            return ProviderSelection(
                provider=provider,
                model_id=model_id,
                request_model=model_id,
                model_meta=meta,
                rewritten=False,
                reason="exact",
            )

    if "/" in model_id:
        prefix, tail = model_id.split("/", 1)
        for provider in providers:
            if provider.id != prefix:
                continue
            alias_target = _aliases(provider).get(tail, tail)
            return ProviderSelection(
                provider=provider,
                model_id=alias_target,
                request_model=model_id,
                model_meta=_find_model(provider, alias_target),
                rewritten=alias_target != model_id,
                reason="provider_prefix",
            )

    for provider in providers:
        if provider.models:
            target = provider.models[0].model_id
            return ProviderSelection(
                provider=provider,
                model_id=target,
                request_model=model_id,
                model_meta=provider.models[0],
                rewritten=target != model_id,
                reason="fallback",
            )

    if providers:
        return ProviderSelection(
            provider=providers[0],
            model_id=model_id,
            request_model=model_id,
            rewritten=False,
            reason="fallback",
        )

    raise ValueError(f"No provider found for model '{model_id}'")


def find_provider_for_model(
    providers: list[ProviderRuntime],
    model_id: str,
) -> tuple[ProviderRuntime, ModelMeta | None]:
    """Find the provider and model metadata for a given model ID.

    Compatibility wrapper around resolve_provider_for_model().
    """
    selection = resolve_provider_for_model(providers, model_id)
    return selection.provider, selection.model_meta


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
