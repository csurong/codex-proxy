"""Tests for FastAPI admin API endpoints."""

import asyncio
import tempfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import codex_proxy.app as app_module
from codex_proxy.config import Config
from codex_proxy.app import app, init_app, mount_static
from codex_proxy.types import (
    ChatChoice,
    ChatChoiceMessage,
    ChatResponse,
    ChatStreamChoice,
    ChatStreamChunk,
    ChatStreamDelta,
    ResponsesRequest,
)


@pytest.fixture
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(app_module, "apply_codex_config", lambda *args, **kwargs: {
            "changed": True,
            "message": "mocked",
        })
        cfg = Config(data_dir=tmpdir)
        init_app(cfg)
        mount_static()
        with TestClient(app) as c:
            yield c


def test_get_status(client):
    resp = client.get("/admin/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "running" in data
    assert "port" in data


def test_status_includes_sidecar_instance_id(client, monkeypatch):
    monkeypatch.setenv("CODEX_PROXY_INSTANCE_ID", "test-instance")

    resp = client.get("/admin/api/status")

    assert resp.status_code == 200
    assert resp.json()["instance_id"] == "test-instance"


def test_lifespan_initializes_db_when_app_is_served_directly(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_db_conn", None)
    monkeypatch.setattr(app_module, "_config", None)
    monkeypatch.setattr(app_module, "load_config", lambda: Config(data_dir=str(tmp_path)))

    with TestClient(app_module.app) as direct_client:
        resp = direct_client.get("/admin/api/providers")
        ui_resp = direct_client.get("/admin/")

    assert resp.status_code == 200
    assert ui_resp.status_code == 200
    ids = {p["id"] for p in resp.json()}
    assert {"mimo", "vllm"} <= ids


def test_get_providers(client):
    resp = client.get("/admin/api/providers")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2  # mimo + vllm
    ids = {p["id"] for p in data}
    assert "mimo" in ids
    assert "vllm" in ids


def test_get_providers_does_not_expose_raw_api_key(client):
    resp = client.patch("/admin/api/providers/mimo", json={"api_key": "sk-secret-value"})
    assert resp.status_code == 200

    resp = client.get("/admin/api/providers")
    assert resp.status_code == 200
    mimo = next(p for p in resp.json() if p["id"] == "mimo")
    assert "api_key" not in mimo
    assert mimo["api_key_preview"] == "sk-sec***"


def test_blank_provider_key_update_preserves_existing_key(client):
    resp = client.patch("/admin/api/providers/mimo", json={"api_key": "sk-secret-value"})
    assert resp.status_code == 200

    resp = client.patch("/admin/api/providers/mimo", json={
        "display_name": "MiMo Updated",
        "api_key": "",
    })
    assert resp.status_code == 200

    resp = client.get("/admin/api/providers")
    mimo = next(p for p in resp.json() if p["id"] == "mimo")
    assert mimo["display_name"] == "MiMo Updated"
    assert mimo["api_key_preview"] == "sk-sec***"


def test_create_custom_provider(client):
    resp = client.post("/admin/api/providers", json={
        "type": "custom",
        "display_name": "Test",
        "base_url": "http://localhost:8000/v1",
        "api_key": "sk-test",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "custom"
    assert data["base_url"] == "http://localhost:8000/v1"


def test_update_provider(client):
    resp = client.patch("/admin/api/providers/mimo", json={"display_name": "MiMo Updated"})
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "MiMo Updated"


def test_delete_custom_provider(client):
    # Create then delete
    resp = client.post("/admin/api/providers", json={"type": "custom", "display_name": "Tmp"})
    pid = resp.json()["id"]
    resp = client.delete(f"/admin/api/providers/{pid}")
    assert resp.status_code == 200


def test_cannot_delete_builtin(client):
    resp = client.delete("/admin/api/providers/mimo")
    assert resp.status_code == 400


def test_get_models(client):
    resp = client.get("/admin/api/models")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 5  # 5 MiMo models


def test_add_model(client):
    resp = client.post("/admin/api/models", json={
        "provider_id": "vllm",
        "model_id": "qwq-32b",
        "display_name": "QwQ 32B",
        "supports_reasoning": 1,
    })
    assert resp.status_code == 200
    assert resp.json()["model_id"] == "qwq-32b"


def test_get_logs(client):
    resp = client.get("/admin/api/logs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_log_stats(client):
    resp = client.get("/admin/api/logs/stats")
    assert resp.status_code == 200
    assert "total" in resp.json()


def test_get_settings(client):
    resp = client.get("/admin/api/settings")
    assert resp.status_code == 200


def test_update_settings(client):
    resp = client.patch("/admin/api/settings", json={"log_max_rows": "500"})
    assert resp.status_code == 200


def test_get_models_v1(client):
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert len(data["data"]) >= 5


def test_proxy_start_stop(client):
    resp = client.post("/admin/api/proxy/start")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    resp = client.get("/admin/api/status")
    assert resp.json()["running"] is True

    resp = client.post("/admin/api/proxy/stop")
    assert resp.status_code == 200
    resp = client.get("/admin/api/status")
    assert resp.json()["running"] is False


def test_proxy_responses_rejects_when_proxy_stopped(client, monkeypatch):
    monkeypatch.setattr(app_module, "_proxy_running", False)

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("upstream should not be called while proxy is stopped")

    monkeypatch.setattr(app_module, "call_upstream", fail_if_called)

    resp = client.post("/v1/responses", json={"model": "mimo-v2.5-pro", "input": "hi"})
    assert resp.status_code == 503
    assert resp.json()["error"] == "Proxy is stopped"


def test_active_model_routes_to_its_provider_when_model_ids_overlap(client, monkeypatch):
    monkeypatch.setattr(app_module, "_proxy_running", True)
    captured = {}

    provider = client.post("/admin/api/providers", json={
        "type": "custom",
        "display_name": "Custom",
        "base_url": "http://custom.example/v1",
    }).json()
    model = client.post("/admin/api/models", json={
        "provider_id": provider["id"],
        "model_id": "mimo-v2.5-pro",
        "display_name": "Custom MiMo Name",
    }).json()
    client.patch("/admin/api/settings", json={"active_model_id": str(model["id"])})

    async def fake_call_upstream(base_url, api_key, body, timeout=120.0, retries=1):
        captured["base_url"] = base_url
        captured["body"] = body
        return ChatResponse(
            id="chatcmpl-test",
            model=body["model"],
            choices=[ChatChoice(message=ChatChoiceMessage(content="ok"))],
        )

    monkeypatch.setattr(app_module, "call_upstream", fake_call_upstream)

    resp = client.post("/v1/responses", json={"model": "mimo-v2.5-pro", "input": "hi"})
    assert resp.status_code == 200
    assert captured["base_url"] == "http://custom.example/v1"
    assert captured["body"]["model"] == "mimo-v2.5-pro"


def test_request_model_routes_to_custom_provider_when_it_shadows_builtin(client, monkeypatch):
    monkeypatch.setattr(app_module, "_proxy_running", True)
    captured = {}

    provider = client.post("/admin/api/providers", json={
        "type": "custom",
        "display_name": "Custom",
        "base_url": "http://custom.example/v1",
    }).json()
    client.post("/admin/api/models", json={
        "provider_id": provider["id"],
        "model_id": "mimo-v2.5-pro",
        "display_name": "Custom MiMo Name",
    })

    async def fake_call_upstream(base_url, api_key, body, timeout=120.0, retries=1):
        captured["base_url"] = base_url
        captured["body"] = body
        return ChatResponse(
            id="chatcmpl-test",
            model=body["model"],
            choices=[ChatChoice(message=ChatChoiceMessage(content="ok"))],
        )

    monkeypatch.setattr(app_module, "call_upstream", fake_call_upstream)

    resp = client.post("/v1/responses", json={"model": "mimo-v2.5-pro", "input": "hi"})

    assert resp.status_code == 200
    assert captured["base_url"] == "http://custom.example/v1"
    assert captured["body"]["model"] == "mimo-v2.5-pro"


def test_request_model_alias_rewrites_to_configured_upstream_model(client, monkeypatch):
    monkeypatch.setattr(app_module, "_proxy_running", True)
    captured = {}

    provider = client.post("/admin/api/providers", json={
        "type": "custom",
        "display_name": "Local",
        "base_url": "http://local.example/v1",
        "config": {"aliases": {"gpt-5": "local-qwen"}},
    }).json()
    client.post("/admin/api/models", json={
        "provider_id": provider["id"],
        "model_id": "local-qwen",
        "display_name": "Local Qwen",
    })

    async def fake_call_upstream(base_url, api_key, body, timeout=120.0, retries=1):
        captured["base_url"] = base_url
        captured["body"] = body
        return ChatResponse(
            id="chatcmpl-test",
            model=body["model"],
            choices=[ChatChoice(message=ChatChoiceMessage(content="ok"))],
        )

    monkeypatch.setattr(app_module, "call_upstream", fake_call_upstream)

    resp = client.post("/v1/responses", json={"model": "gpt-5", "input": "hi"})

    assert resp.status_code == 200
    assert captured["base_url"] == "http://local.example/v1"
    assert captured["body"]["model"] == "local-qwen"


def test_mimo_payg_forwards_web_search_tool(client, monkeypatch):
    monkeypatch.setattr(app_module, "_proxy_running", True)
    client.patch("/admin/api/providers/mimo", json={"api_key": "sk-payg-test"})
    captured = {}

    async def fake_call_upstream(base_url, api_key, body, timeout=120.0, retries=1):
        captured["base_url"] = base_url
        captured["body"] = body
        return ChatResponse(
            id="chatcmpl-test",
            model=body["model"],
            choices=[ChatChoice(message=ChatChoiceMessage(content="ok"))],
        )

    monkeypatch.setattr(app_module, "call_upstream", fake_call_upstream)

    resp = client.post("/v1/responses", json={
        "model": "mimo-v2.5-pro",
        "input": "hi",
        "tools": [{"type": "web_search"}],
    })

    assert resp.status_code == 200
    assert "api.xiaomimimo.com" in captured["base_url"]
    assert captured["body"]["tools"] == [{"type": "web_search"}]


def test_mimo_token_plan_drops_web_search_tool(client, monkeypatch):
    monkeypatch.setattr(app_module, "_proxy_running", True)
    client.patch("/admin/api/providers/mimo", json={"api_key": "tp-token-plan-test"})
    captured = {}

    async def fake_call_upstream(base_url, api_key, body, timeout=120.0, retries=1):
        captured["base_url"] = base_url
        captured["body"] = body
        return ChatResponse(
            id="chatcmpl-test",
            model=body["model"],
            choices=[ChatChoice(message=ChatChoiceMessage(content="ok"))],
        )

    monkeypatch.setattr(app_module, "call_upstream", fake_call_upstream)

    resp = client.post("/v1/responses", json={
        "model": "mimo-v2.5-pro",
        "input": "hi",
        "tools": [{"type": "web_search"}],
    })

    assert resp.status_code == 200
    assert "token-plan" in captured["base_url"]
    assert "tools" not in captured["body"]


def test_image_request_routes_to_same_provider_vision_model(client, monkeypatch):
    monkeypatch.setattr(app_module, "_proxy_running", True)
    captured = {}

    async def fake_call_upstream(base_url, api_key, body, timeout=120.0, retries=1):
        captured["body"] = body
        return ChatResponse(
            id="chatcmpl-test",
            model=body["model"],
            choices=[ChatChoice(message=ChatChoiceMessage(content="ok"))],
        )

    monkeypatch.setattr(app_module, "call_upstream", fake_call_upstream)

    resp = client.post("/v1/responses", json={
        "model": "mimo-v2.5-pro",
        "input": [{
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "describe this"},
                {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
            ],
        }],
    })

    assert resp.status_code == 200
    assert captured["body"]["model"] == "mimo-v2.5"
    assert captured["body"]["messages"][0]["content"][1]["type"] == "image_url"


def test_text_request_after_historical_image_uses_selected_text_model(client, monkeypatch):
    monkeypatch.setattr(app_module, "_proxy_running", True)
    captured = {}

    async def fake_call_upstream(base_url, api_key, body, timeout=120.0, retries=1):
        captured["body"] = body
        return ChatResponse(
            id="chatcmpl-test",
            model=body["model"],
            choices=[ChatChoice(message=ChatChoiceMessage(content="ok"))],
        )

    monkeypatch.setattr(app_module, "call_upstream", fake_call_upstream)

    resp = client.post("/v1/responses", json={
        "model": "mimo-v2.5-pro",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "describe this"},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                ],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "It is a chart."}],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "summarize that in one sentence"}],
            },
        ],
    })

    assert resp.status_code == 200
    assert captured["body"]["model"] == "mimo-v2.5-pro"
    assert all(
        part.get("type") != "image_url"
        for message in captured["body"]["messages"]
        if isinstance(message.get("content"), list)
        for part in message["content"]
    )


def test_history_image_setting_keeps_vision_model_for_followup_text(client, monkeypatch):
    monkeypatch.setattr(app_module, "_proxy_running", True)
    captured = {}

    client.patch("/admin/api/settings", json={"vision_route_include_history": "1"})

    async def fake_call_upstream(base_url, api_key, body, timeout=120.0, retries=1):
        captured["body"] = body
        return ChatResponse(
            id="chatcmpl-test",
            model=body["model"],
            choices=[ChatChoice(message=ChatChoiceMessage(content="ok"))],
        )

    monkeypatch.setattr(app_module, "call_upstream", fake_call_upstream)

    resp = client.post("/v1/responses", json={
        "model": "mimo-v2.5-pro",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "describe this"},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                ],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "It is a chart."}],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "explain the top-left area"}],
            },
        ],
    })

    assert resp.status_code == 200
    assert captured["body"]["model"] == "mimo-v2.5"
    assert any(
        part.get("type") == "image_url"
        for message in captured["body"]["messages"]
        if isinstance(message.get("content"), list)
        for part in message["content"]
    )


def test_image_request_active_model_routes_within_active_provider(client, monkeypatch):
    monkeypatch.setattr(app_module, "_proxy_running", True)
    captured = {}

    provider = client.post("/admin/api/providers", json={
        "type": "custom",
        "display_name": "Vision Custom",
        "base_url": "http://vision.example/v1",
    }).json()
    text_model = client.post("/admin/api/models", json={
        "provider_id": provider["id"],
        "model_id": "text-only",
        "display_name": "Text Only",
    }).json()
    client.post("/admin/api/models", json={
        "provider_id": provider["id"],
        "model_id": "vision-model",
        "display_name": "Vision Model",
        "supports_images": 1,
        "sort_order": 1,
    })
    client.patch("/admin/api/settings", json={"active_model_id": str(text_model["id"])})

    async def fake_call_upstream(base_url, api_key, body, timeout=120.0, retries=1):
        captured["base_url"] = base_url
        captured["body"] = body
        return ChatResponse(
            id="chatcmpl-test",
            model=body["model"],
            choices=[ChatChoice(message=ChatChoiceMessage(content="ok"))],
        )

    monkeypatch.setattr(app_module, "call_upstream", fake_call_upstream)

    resp = client.post("/v1/responses", json={
        "model": "mimo-v2.5-pro",
        "input": [{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}],
        }],
    })

    assert resp.status_code == 200
    assert captured["base_url"] == "http://vision.example/v1"
    assert captured["body"]["model"] == "vision-model"
    assert captured["body"]["messages"][0]["content"][1]["type"] == "image_url"


@pytest.mark.asyncio
async def test_stream_keepalive_timeout_does_not_cancel_upstream_stream(client, monkeypatch):
    async def fake_call_upstream_stream(*args, **kwargs):
        await asyncio.sleep(0.05)
        yield ChatStreamChunk(
            id="chunk-1",
            model="upstream-model",
            choices=[ChatStreamChoice(delta=ChatStreamDelta(content="late"))],
        )
        yield ChatStreamChunk(
            id="chunk-2",
            model="upstream-model",
            choices=[ChatStreamChoice(finish_reason="stop")],
        )

    monkeypatch.setattr(app_module, "call_upstream_stream", fake_call_upstream_stream)

    original_wait_for = asyncio.wait_for
    call_count = 0

    async def cancel_once_then_timeout(awaitable, timeout):
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            return await original_wait_for(awaitable, timeout=0.001)
        return await original_wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(app_module.asyncio, "wait_for", cancel_once_then_timeout)

    resp = await app_module._handle_stream(
        ResponsesRequest(model="mimo-v2.5-pro", input="hi", stream=True),
        SimpleNamespace(api_key=None),
        "http://upstream.example/v1",
        {"model": "mimo-v2.5-pro", "messages": [], "stream": True},
        Config(data_dir="unused"),
        0.0,
        {"provider_id": "custom", "model": "mimo-v2.5-pro", "request_model": "mimo-v2.5-pro", "stream": 1},
    )

    parts = []
    async for chunk in resp.body_iterator:
        parts.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    body = "".join(parts)
    assert ": keepalive" in body
    assert "late" in body
