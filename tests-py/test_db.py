"""Tests for database layer."""

import json
import os
import tempfile
import pytest

from codex_proxy.db import (
    open_db, list_providers, get_provider, create_provider, update_provider, delete_provider,
    list_models, get_model, create_model, update_model, delete_model,
    insert_chat_log, list_chat_logs, get_log_stats,
    get_setting, set_setting, cleanup_logs,
)


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        conn = open_db(tmpdir)
        yield conn
        conn.close()


def test_tables_exist(db):
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "providers" in tables
    assert "models" in tables
    assert "settings" in tables
    assert "chat_logs" in tables
    assert "schema_version" in tables


def test_seed_mimo_provider(db):
    p = get_provider(db, "mimo")
    assert p is not None
    assert p["type"] == "mimo"
    assert p["display_name"] == "MiMo"
    assert "xiaomimimo.com" in p["base_url"]


def test_seed_mimo_models(db):
    models = list_models(db, "mimo")
    assert len(models) == 5
    ids = {m["model_id"] for m in models}
    assert "mimo-v2.5-pro" in ids
    assert "mimo-v2.5" in ids
    assert "mimo-v2-flash" in ids
    # Check vision model
    v = next(m for m in models if m["model_id"] == "mimo-v2.5")
    assert v["supports_images"] == 1
    assert v["supports_reasoning"] == 1
    # Flash has no reasoning
    f = next(m for m in models if m["model_id"] == "mimo-v2-flash")
    assert f["supports_reasoning"] == 0


def test_seed_vllm_provider(db):
    p = get_provider(db, "vllm")
    assert p is not None
    assert p["type"] == "vllm"
    assert p["display_name"] == "Qwen (vLLM)"


def test_create_custom_provider(db):
    p = create_provider(db, {
        "id": "custom_test",
        "type": "custom",
        "display_name": "Test Provider",
        "base_url": "http://localhost:8000/v1",
        "api_key": "sk-test",
    })
    assert p["id"] == "custom_test"
    assert p["base_url"] == "http://localhost:8000/v1"


def test_update_provider(db):
    update_provider(db, "mimo", {"display_name": "MiMo Updated"})
    p = get_provider(db, "mimo")
    assert p["display_name"] == "MiMo Updated"


def test_delete_provider_cascades(db):
    create_provider(db, {"id": "tmp", "type": "custom", "display_name": "Tmp"})
    create_model(db, {"provider_id": "tmp", "model_id": "m1"})
    assert len(list_models(db, "tmp")) == 1
    delete_provider(db, "tmp")
    assert get_provider(db, "tmp") is None
    assert len(list_models(db, "tmp")) == 0


def test_create_model(db):
    m = create_model(db, {
        "provider_id": "vllm",
        "model_id": "qwq-32b",
        "display_name": "QwQ 32B",
        "supports_reasoning": 1,
        "supports_tools": 1,
        "context_window": 131072,
        "max_output_tokens": 32768,
    })
    assert m["model_id"] == "qwq-32b"
    assert m["supports_reasoning"] == 1


def test_update_model(db):
    models = list_models(db, "mimo")
    mid = models[0]["id"]
    update_model(db, mid, {"display_name": "Updated"})
    m = get_model(db, mid)
    assert m["display_name"] == "Updated"


def test_delete_model(db):
    m = create_model(db, {"provider_id": "vllm", "model_id": "tmp-model"})
    assert delete_model(db, m["id"])


def test_chat_log_insert_and_query(db):
    insert_chat_log(db, {
        "provider_id": "mimo",
        "model": "mimo-v2.5-pro",
        "status_code": 200,
        "duration_ms": 1500,
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "stream": 1,
    })
    logs = list_chat_logs(db, limit=10)
    assert len(logs) == 1
    assert logs[0]["model"] == "mimo-v2.5-pro"


def test_log_stats(db):
    for i in range(3):
        insert_chat_log(db, {"prompt_tokens": 100, "completion_tokens": 50})
    stats = get_log_stats(db)
    assert stats["total"] == 3
    assert stats["total_prompt_tokens"] == 300


def test_log_cleanup(db):
    for i in range(1010):
        insert_chat_log(db, {"ts": 1000 + i, "prompt_tokens": 10})
    deleted = cleanup_logs(db, max_rows=1000)
    assert deleted >= 10
    remaining = list_chat_logs(db, limit=9999)
    assert len(remaining) <= 1000


def test_settings_get_set(db):
    assert get_setting(db, "nonexistent") is None
    set_setting(db, "test_key", "test_value")
    assert get_setting(db, "test_key") == "test_value"
    set_setting(db, "test_key", "updated")
    assert get_setting(db, "test_key") == "updated"
