"""Auto-update ~/.codex/config.toml to point at Codex-Proxy proxy."""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .log import get_logger

log = get_logger()

CODEX_DIR = Path.home() / ".codex"
CONFIG_TOML = CODEX_DIR / "config.toml"


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
    """Find the most recent backup and restore it."""
    backups = sorted(CODEX_DIR.glob("config.toml.bak.*"), reverse=True)
    if not backups:
        return {"ok": False, "message": "No backups found"}

    latest = backups[0]
    shutil.copy2(latest, CONFIG_TOML)
    log.info(f"Restored Codex config from {latest}")
    return {"ok": True, "message": f"Restored from {latest.name}"}
