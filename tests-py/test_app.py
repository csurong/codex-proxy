"""Tests for FastAPI admin API endpoints."""

import tempfile
import pytest
from fastapi.testclient import TestClient

from codex_proxy.config import Config
from codex_proxy.app import app, init_app, mount_static


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as tmpdir:
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


def test_get_providers(client):
    resp = client.get("/admin/api/providers")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2  # mimo + vllm
    ids = {p["id"] for p in data}
    assert "mimo" in ids
    assert "vllm" in ids


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
