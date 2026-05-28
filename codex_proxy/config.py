"""Application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 18788
    data_dir: str = ""
    verbose: bool = False
    no_reasoning: bool = False
    open_browser: bool = True
    log_max_rows: int = 1000
    log_max_age_days: int = 7

    @property
    def expose_reasoning(self) -> bool:
        return not self.no_reasoning


def get_data_dir() -> str:
    return str(Path.home() / ".codex-proxy" / "data")


def load_config() -> Config:
    cfg = Config()
    cfg.host = os.environ.get("CODEX_PROXY_HOST", "127.0.0.1")
    cfg.port = int(os.environ.get("CODEX_PROXY_PORT", "18788"))
    cfg.data_dir = get_data_dir()
    cfg.verbose = os.environ.get("CODEX_PROXY_VERBOSE", "").lower() in ("1", "true", "yes")
    cfg.no_reasoning = os.environ.get("CODEX_PROXY_NO_REASONING", "").lower() in ("1", "true", "yes")
    cfg.open_browser = os.environ.get("CODEX_PROXY_NO_BROWSER", "").lower() not in ("1", "true", "yes")
    return cfg
