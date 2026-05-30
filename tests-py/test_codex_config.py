"""Tests for Codex config file integration."""

from __future__ import annotations

import json

from codex_proxy import codex_config


def _use_temp_codex_dir(monkeypatch, tmp_path):
    codex_dir = tmp_path / ".codex"
    monkeypatch.setattr(codex_config, "CODEX_DIR", codex_dir)
    monkeypatch.setattr(codex_config, "CONFIG_TOML", codex_dir / "config.toml")
    monkeypatch.setattr(codex_config, "AUTH_JSON", codex_dir / "auth.json")
    return codex_dir


def test_apply_codex_config_writes_auth_and_config_with_paired_backups(monkeypatch, tmp_path):
    codex_dir = _use_temp_codex_dir(monkeypatch, tmp_path)
    codex_dir.mkdir()
    codex_config.AUTH_JSON.write_text('{"OPENAI_API_KEY":"real-openai"}', encoding="utf-8")
    codex_config.CONFIG_TOML.write_text('model = "gpt-5"\n', encoding="utf-8")

    result = codex_config.apply_codex_config(
        "http://127.0.0.1:8080/v1",
        provider_key="mimo",
        provider_name="MiMo",
        model_id="mimo-v2.5-pro",
        context_window=1_000_000,
        max_output_tokens=131_072,
        supports_reasoning=True,
    )

    assert result["changed"] is True
    assert result["auth_backup_path"]
    assert result["toml_backup_path"]
    assert codex_config.AUTH_JSON.exists()
    assert codex_config.CONFIG_TOML.exists()

    auth = json.loads(codex_config.AUTH_JSON.read_text(encoding="utf-8"))
    toml = codex_config.CONFIG_TOML.read_text(encoding="utf-8")
    assert auth == {"OPENAI_API_KEY": "codex-proxy-local"}
    assert 'model_provider = "mimo"' in toml
    assert 'model = "mimo-v2.5-pro"' in toml
    assert 'base_url = "http://127.0.0.1:8080/v1"' in toml
    assert 'wire_api = "responses"' in toml
    assert "requires_openai_auth = true" in toml
    assert "model_context_window = 1000000" in toml
    assert "model_max_output_tokens = 131072" in toml
    assert "model_supports_reasoning_summaries = true" in toml


def test_apply_codex_config_preserves_unrelated_existing_toml_sections(monkeypatch, tmp_path):
    codex_dir = _use_temp_codex_dir(monkeypatch, tmp_path)
    codex_dir.mkdir()
    codex_config.CONFIG_TOML.write_text(
        'notify = ["existing"]\n\n[mcp_servers.demo]\ncommand = "demo"\n',
        encoding="utf-8",
    )

    codex_config.apply_codex_config(
        "http://127.0.0.1:8080/v1",
        provider_key="mimo",
        provider_name="MiMo",
        model_id="mimo-v2.5-pro",
    )

    toml = codex_config.CONFIG_TOML.read_text(encoding="utf-8")
    assert 'notify = ["existing"]' in toml
    assert "[mcp_servers.demo]" in toml
    assert 'command = "demo"' in toml
    assert 'model_provider = "mimo"' in toml
    assert "[model_providers.mimo]" in toml


def test_restore_codex_backup_restores_paired_state_and_removes_new_file(monkeypatch, tmp_path):
    codex_dir = _use_temp_codex_dir(monkeypatch, tmp_path)
    codex_dir.mkdir()
    codex_config.AUTH_JSON.write_text('{"OPENAI_API_KEY":"real-openai"}', encoding="utf-8")

    codex_config.apply_codex_config(
        "http://127.0.0.1:8080/v1",
        provider_key="mimo",
        provider_name="MiMo",
        model_id="mimo-v2.5-pro",
    )

    result = codex_config.restore_codex_backup()

    assert result["ok"] is True
    assert json.loads(codex_config.AUTH_JSON.read_text(encoding="utf-8")) == {
        "OPENAI_API_KEY": "real-openai",
    }
    assert not codex_config.CONFIG_TOML.exists()


def test_restore_legacy_config_only_backup_preserves_existing_auth(monkeypatch, tmp_path):
    codex_dir = _use_temp_codex_dir(monkeypatch, tmp_path)
    codex_dir.mkdir()
    codex_config.AUTH_JSON.write_text('{"OPENAI_API_KEY":"real-openai"}', encoding="utf-8")
    codex_config.CONFIG_TOML.write_text('model = "current"\n', encoding="utf-8")
    legacy_backup = codex_dir / "config.toml.bak.123"
    legacy_backup.write_text('model = "legacy"\n', encoding="utf-8")

    result = codex_config.restore_codex_backup()

    assert result["ok"] is True
    assert codex_config.CONFIG_TOML.read_text(encoding="utf-8") == 'model = "legacy"\n'
    assert json.loads(codex_config.AUTH_JSON.read_text(encoding="utf-8")) == {
        "OPENAI_API_KEY": "real-openai",
    }
