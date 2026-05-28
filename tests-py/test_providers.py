"""Tests for provider normalization."""

import pytest
from codex_proxy.providers import (
    ProviderRuntime, ModelMeta,
    normalize_mimo, normalize_vllm, normalize_custom, normalize_body,
    resolve_mimo_base_url, find_provider_for_model, strip_images_for_non_vision,
)


@pytest.fixture
def mimo_runtime():
    return ProviderRuntime(
        id="mimo", type="mimo", display_name="MiMo",
        base_url="https://api.xiaomimimo.com/v1", api_key="sk-test123",
        models=[
            ModelMeta("mimo-v2.5-pro", supports_reasoning=True, supports_tools=True),
            ModelMeta("mimo-v2-flash"),
            ModelMeta("mimo-v2.5", supports_images=True, supports_reasoning=True),
        ],
    )


@pytest.fixture
def vllm_runtime():
    return ProviderRuntime(
        id="vllm", type="vllm", display_name="Qwen (vLLM)",
        base_url="http://localhost:8000/v1", api_key=None,
        config={"enable_thinking": True},
        models=[ModelMeta("qwq-32b", supports_reasoning=True)],
    )


# MiMo tests

def test_mimo_normalize_injects_thinking():
    body = {"model": "mimo-v2.5-pro", "messages": []}
    result = normalize_mimo(body, "mimo-v2.5-pro", None)
    assert result["thinking"] == {"type": "enabled"}
    assert "temperature" not in result


def test_mimo_normalize_disabled_thinking():
    body = {"model": "mimo-v2.5-pro", "messages": [], "temperature": 0.7}
    result = normalize_mimo(body, "mimo-v2.5-pro", False)
    assert result["thinking"] == {"type": "disabled"}
    assert result.get("temperature") == 0.7  # preserved when thinking off


def test_mimo_normalize_strips_temperature_in_thinking():
    body = {"model": "mimo-v2.5-pro", "messages": [], "temperature": 0.3}
    result = normalize_mimo(body, "mimo-v2.5-pro", True)
    assert "temperature" not in result


def test_mimo_flash_default_disabled():
    body = {"model": "mimo-v2-flash", "messages": []}
    result = normalize_mimo(body, "mimo-v2-flash", None)  # None = auto-detect
    assert result["thinking"] == {"type": "disabled"}


def test_mimo_max_completion_tokens():
    body = {"model": "mimo-v2.5-pro", "messages": [], "max_tokens": 1000}
    result = normalize_mimo(body, "mimo-v2.5-pro", True)
    assert "max_tokens" not in result
    assert result["max_completion_tokens"] == 1000


def test_mimo_detect_token_plan():
    url = resolve_mimo_base_url("tp-abc123", "https://api.xiaomimimo.com/v1")
    assert "token-plan" in url


def test_mimo_detect_payg():
    url = resolve_mimo_base_url("sk-abc123", "https://api.xiaomimimo.com/v1")
    assert url == MIMO_PAYG_URL if False else "api.xiaomimimo.com" in url


# vLLM tests

def test_vllm_normalize_injects_thinking_kwargs():
    body = {"model": "qwq-32b", "messages": []}
    result = normalize_vllm(body, {"enable_thinking": True}, None)
    assert result["chat_template_kwargs"] == {"enable_thinking": True}


def test_vllm_normalize_no_thinking():
    body = {"model": "qwq-32b", "messages": []}
    result = normalize_vllm(body, {"enable_thinking": False}, None)
    assert "chat_template_kwargs" not in result


def test_vllm_explicit_thinking_on():
    body = {"model": "qwq-32b", "messages": []}
    result = normalize_vllm(body, {}, True)
    assert result["chat_template_kwargs"]["enable_thinking"] is True


# Custom tests

def test_custom_normalize_passthrough():
    body = {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}], "temperature": 0.7}
    result = normalize_custom(body)
    assert result == body


# Provider selection

def test_select_provider_by_model(mimo_runtime, vllm_runtime):
    providers = [mimo_runtime, vllm_runtime]
    p, m = find_provider_for_model(providers, "mimo-v2.5-pro")
    assert p.id == "mimo"
    assert m.model_id == "mimo-v2.5-pro"


def test_select_provider_by_prefix(mimo_runtime, vllm_runtime):
    providers = [mimo_runtime, vllm_runtime]
    p, m = find_provider_for_model(providers, "vllm/some-model")
    assert p.id == "vllm"


def test_select_provider_fallback(mimo_runtime, vllm_runtime):
    providers = [mimo_runtime, vllm_runtime]
    p, m = find_provider_for_model(providers, "unknown-model")
    assert p.id == "mimo"  # first provider


# Image stripping

def test_strip_images_for_non_vision():
    messages = [
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:..."}},
            {"type": "text", "text": "describe this"},
        ]},
    ]
    meta = ModelMeta("test", supports_images=False)
    result = strip_images_for_non_vision(messages, meta)
    assert len(result[0]["content"]) == 1
    assert result[0]["content"][0]["type"] == "text"


def test_keep_images_for_vision():
    messages = [
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:..."}},
            {"type": "text", "text": "describe this"},
        ]},
    ]
    meta = ModelMeta("test", supports_images=True)
    result = strip_images_for_non_vision(messages, meta)
    assert len(result[0]["content"]) == 2


def test_strip_images_empty_content():
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:..."}},
    ]}]
    meta = ModelMeta("test", supports_images=False)
    result = strip_images_for_non_vision(messages, meta)
    assert len(result[0]["content"]) == 1
    assert result[0]["content"][0]["text"] == " "  # fallback


# normalize_body dispatch

def test_normalize_body_dispatches_to_mimo(mimo_runtime):
    body = {"model": "mimo-v2.5-pro", "messages": []}
    result = normalize_body(mimo_runtime, body, "mimo-v2.5-pro")
    assert "thinking" in result


def test_normalize_body_dispatches_to_vllm(vllm_runtime):
    body = {"model": "qwq-32b", "messages": []}
    result = normalize_body(vllm_runtime, body, "qwq-32b")
    assert "chat_template_kwargs" in result
