"""Auto-update ~/.codex/config.toml to point at Codex-Proxy proxy."""

from __future__ import annotations

import os
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .log import get_logger

log = get_logger()

CODEX_DIR = Path.home() / ".codex"
CONFIG_TOML = CODEX_DIR / "config.toml"
AUTH_JSON = CODEX_DIR / "auth.json"
LOCAL_AUTH_SENTINEL = "codex-proxy-local"


def get_codex_config_path() -> Path:
    return CONFIG_TOML


def read_codex_config() -> str | None:
    """Read the current codex config.toml, return None if not found."""
    if CONFIG_TOML.exists():
        return CONFIG_TOML.read_text(encoding="utf-8")
    return None


def backup_codex_config() -> Path | None:
    """Create a timestamped backup of config.toml. Returns backup path."""
    if not CONFIG_TOML.exists():
        return None
    ts = int(time.time())
    backup = CONFIG_TOML.with_suffix(f".toml.bak.{ts}")
    shutil.copy2(CONFIG_TOML, backup)
    log.info(f"Backed up Codex config to {backup}")
    return backup


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _backup_path(path: Path, ts: int) -> Path:
    return path.with_name(f"{path.name}.bak.{ts}")


def _backup_file(path: Path, ts: int) -> Path | None:
    if not path.exists():
        return None
    backup = _backup_path(path, ts)
    shutil.copy2(path, backup)
    return backup


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _set_top_level_key(head: str, key: str, line: str | None) -> str:
    pattern = re.compile(rf"(?m)^\s*{re.escape(key)}\s*=.*(?:\n|$)")
    head = pattern.sub("", head)
    if line is None:
        return head
    if head and not head.endswith("\n"):
        head += "\n"
    return head + line + "\n"


def _provider_section(
    proxy_url: str,
    provider_key: str,
    provider_name: str,
) -> str:
    return "\n".join([
        f"[model_providers.{provider_key}]",
        f"name = {_toml_string(provider_name)}",
        f"base_url = {_toml_string(proxy_url)}",
        'wire_api = "responses"',
        "requires_openai_auth = true",
        "request_max_retries = 1",
        "",
    ])


def merge_codex_config_toml(
    existing: str,
    proxy_url: str,
    provider_key: str,
    provider_name: str,
    model_id: str,
    context_window: int | None = None,
    max_output_tokens: int | None = None,
    supports_reasoning: bool = False,
) -> str:
    """Preserve unrelated Codex config while updating proxy model settings."""
    first_section = re.search(r"(?m)^\s*\[", existing)
    if first_section:
        head = existing[:first_section.start()]
        rest = existing[first_section.start():]
    else:
        head = existing
        rest = ""

    head = _set_top_level_key(head, "model_provider", f"model_provider = {_toml_string(provider_key)}")
    head = _set_top_level_key(head, "model", f"model = {_toml_string(model_id)}")

    tuning = {
        "model_context_window": f"model_context_window = {int(context_window)}" if context_window else None,
        "model_max_output_tokens": f"model_max_output_tokens = {int(max_output_tokens)}" if max_output_tokens else None,
        "model_supports_reasoning_summaries": "model_supports_reasoning_summaries = true" if supports_reasoning else None,
        "model_reasoning_summary": 'model_reasoning_summary = "auto"' if supports_reasoning else None,
    }
    for key, line in tuning.items():
        head = _set_top_level_key(head, key, line)

    section = _provider_section(proxy_url, provider_key, provider_name)
    section_pattern = re.compile(
        rf"(?ms)^\s*\[model_providers\.{re.escape(provider_key)}\]\s*\n.*?(?=^\s*\[|\Z)"
    )
    if section_pattern.search(rest):
        rest = section_pattern.sub(section, rest, count=1)
    else:
        if rest and not rest.startswith("\n"):
            rest = "\n" + rest
        rest = section + rest

    return head.rstrip() + "\n\n" + rest.lstrip()


def build_codex_config_toml(
    proxy_url: str,
    provider_key: str,
    provider_name: str,
    model_id: str,
    context_window: int | None = None,
    max_output_tokens: int | None = None,
    supports_reasoning: bool = False,
) -> str:
    """Build the full config.toml contents for local Codex proxy use."""
    return merge_codex_config_toml(
        "",
        proxy_url,
        provider_key,
        provider_name,
        model_id,
        context_window,
        max_output_tokens,
        supports_reasoning,
    )


def apply_codex_config(
    proxy_url: str,
    provider_key: str = "mimo",
    provider_name: str = "MiMo",
    model_id: str = "mimo-v2.5-pro",
    context_window: int | None = None,
    max_output_tokens: int | None = None,
    supports_reasoning: bool = False,
) -> dict[str, Any]:
    """Write paired auth.json/config.toml files for Codex and back up prior state."""
    ts = int(time.time() * 1000)
    CODEX_DIR.mkdir(parents=True, exist_ok=True)

    auth_backup = _backup_file(AUTH_JSON, ts)
    toml_backup = _backup_file(CONFIG_TOML, ts)

    auth_json = json.dumps({"OPENAI_API_KEY": LOCAL_AUTH_SENTINEL}, indent=2) + "\n"
    existing_toml = CONFIG_TOML.read_text(encoding="utf-8") if CONFIG_TOML.exists() else ""
    config_toml = merge_codex_config_toml(
        existing_toml,
        proxy_url,
        provider_key=provider_key,
        provider_name=provider_name,
        model_id=model_id,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        supports_reasoning=supports_reasoning,
    )

    _atomic_write(AUTH_JSON, auth_json)
    _atomic_write(CONFIG_TOML, config_toml)

    log.info(f"Applied Codex config for provider={provider_key} model={model_id}")
    return {
        "changed": True,
        "backup_ts": ts,
        "auth_backup_path": str(auth_backup) if auth_backup else None,
        "toml_backup_path": str(toml_backup) if toml_backup else None,
        "message": f"Configured Codex for {provider_name} via {proxy_url}",
    }


def update_codex_proxy_url(proxy_url: str, provider_key: str = "mimo") -> dict[str, Any]:
    """Update config.toml to point the MiMo provider at Codex-Proxy proxy.
    
    Returns dict with: changed (bool), backup_path, message.
    """
    result: dict[str, Any] = {"changed": False, "backup_path": None, "message": ""}

    content = read_codex_config()
    if content is None:
        result["message"] = f"Config not found at {CONFIG_TOML}"
        return result

    # Pattern: find [model_providers.mimo] section and its base_url line
    section_header = f"[model_providers.{provider_key}]"
    if section_header not in content:
        result["message"] = f"Section {section_header} not found in config"
        return result

    # Check if already pointing at our proxy
    pattern = rf'(\[model_providers\.{re.escape(provider_key)}\][^\[]*?base_url\s*=\s*")([^"]*?)(")'
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        result["message"] = f"base_url not found in {section_header}"
        return result

    current_url = match.group(2)
    if current_url == proxy_url:
        result["message"] = f"Already pointing at {proxy_url}"
        result["changed"] = False
        return result

    # Backup before modifying
    backup = backup_codex_config()
    result["backup_path"] = str(backup) if backup else None

    # Replace base_url
    new_content = content[:match.start(2)] + proxy_url + content[match.end(2):]

    CONFIG_TOML.write_text(new_content, encoding="utf-8")
    result["changed"] = True
    result["message"] = f"Updated {section_header} base_url: {current_url} → {proxy_url}"
    log.info(result["message"])
    return result


def restore_codex_backup() -> dict[str, Any]:
    """Find the most recent paired auth/config backup and restore it."""
    def backup_map(path: Path) -> dict[int, Path]:
        result: dict[int, Path] = {}
        for backup in CODEX_DIR.glob(f"{path.name}.bak.*"):
            try:
                ts = int(backup.name.rsplit(".bak.", 1)[1])
            except (IndexError, ValueError):
                continue
            result[ts] = backup
        return result

    auth_backups = backup_map(AUTH_JSON)
    toml_backups = backup_map(CONFIG_TOML)
    timestamps = sorted(set(auth_backups) | set(toml_backups), reverse=True)
    if not timestamps:
        return {"ok": False, "message": "No backups found"}

    latest = timestamps[0]
    auth_backup = auth_backups.get(latest)
    toml_backup = toml_backups.get(latest)

    if auth_backup:
        _atomic_write(AUTH_JSON, auth_backup.read_text(encoding="utf-8"))
    elif not toml_backup and AUTH_JSON.exists():
        AUTH_JSON.unlink()

    if toml_backup:
        _atomic_write(CONFIG_TOML, toml_backup.read_text(encoding="utf-8"))
    elif auth_backup and CONFIG_TOML.exists():
        CONFIG_TOML.unlink()

    log.info(f"Restored Codex config backup pair {latest}")
    return {"ok": True, "message": f"Restored backup pair {latest}", "backup_ts": latest}
