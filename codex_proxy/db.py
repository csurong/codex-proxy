"""SQLite database: migrations, seeding, CRUD."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .log import get_logger

log = get_logger()

MIGRATIONS = [
    # v1: core tables
    """
CREATE TABLE IF NOT EXISTS providers (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  display_name TEXT NOT NULL,
  base_url TEXT NOT NULL DEFAULT '',
  api_key TEXT,
  config_json TEXT,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS models (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
  model_id TEXT NOT NULL,
  display_name TEXT,
  supports_images INTEGER NOT NULL DEFAULT 0,
  supports_reasoning INTEGER NOT NULL DEFAULT 0,
  supports_tools INTEGER NOT NULL DEFAULT 0,
  context_window INTEGER,
  max_output_tokens INTEGER,
  sort_order INTEGER NOT NULL DEFAULT 0,
  UNIQUE(provider_id, model_id)
);
CREATE INDEX IF NOT EXISTS idx_models_provider ON models(provider_id, sort_order);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  provider_id TEXT,
  model TEXT,
  status_code INTEGER,
  duration_ms INTEGER,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  stream INTEGER NOT NULL DEFAULT 0,
  error_snippet TEXT,
  request_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_logs_ts ON chat_logs(ts DESC);
""",
    # v2: add upstream_model to chat_logs
    """
ALTER TABLE chat_logs ADD COLUMN upstream_model TEXT;
""",
    # v3: add request_model to chat_logs
    """
ALTER TABLE chat_logs ADD COLUMN request_model TEXT;
""",]

# Built-in MiMo models
MIMO_MODELS = [
    {"model_id": "mimo-v2.5-pro", "display_name": "MiMo V2.5 Pro", "supports_images": 0, "supports_reasoning": 1, "supports_tools": 1, "context_window": 1_000_000, "max_output_tokens": 131_072},
    {"model_id": "mimo-v2-pro", "display_name": "MiMo V2 Pro", "supports_images": 0, "supports_reasoning": 1, "supports_tools": 1, "context_window": 1_000_000, "max_output_tokens": 131_072},
    {"model_id": "mimo-v2.5", "display_name": "MiMo V2.5 (Vision)", "supports_images": 1, "supports_reasoning": 1, "supports_tools": 1, "context_window": 1_000_000, "max_output_tokens": 32_768},
    {"model_id": "mimo-v2-omni", "display_name": "MiMo V2 Omni", "supports_images": 1, "supports_reasoning": 1, "supports_tools": 1, "context_window": 1_000_000, "max_output_tokens": 32_768},
    {"model_id": "mimo-v2-flash", "display_name": "MiMo V2 Flash", "supports_images": 0, "supports_reasoning": 0, "supports_tools": 1, "context_window": 1_000_000, "max_output_tokens": 65_536},
]


def open_db(data_dir: str) -> sqlite3.Connection:
    """Open (or create) the SQLite DB, run migrations, seed builtins."""
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    db_path = os.path.join(data_dir, "data.db")
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _run_migrations(conn)
    _seed_builtins(conn)
    conn.commit()
    log.info(f"Database opened at {db_path}")
    return conn


def _run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    current = row["v"] if row and row["v"] else 0
    for i, sql in enumerate(MIGRATIONS, 1):
        if i <= current:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)", (i, int(time.time()))
        )
        log.debug(f"Applied schema migration v{i}")


def _seed_builtins(conn: sqlite3.Connection) -> None:
    now = int(time.time())

    # MiMo provider
    conn.execute(
        """INSERT INTO providers (id, type, display_name, base_url, api_key, config_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name, base_url=excluded.base_url""",
        ("mimo", "mimo", "MiMo", "https://api.xiaomimimo.com/v1", None, None, now),
    )
    # MiMo models
    for order, m in enumerate(MIMO_MODELS):
        conn.execute(
            """INSERT INTO models (provider_id, model_id, display_name, supports_images, supports_reasoning,
                   supports_tools, context_window, max_output_tokens, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(provider_id, model_id) DO UPDATE SET
                   display_name=excluded.display_name, supports_images=excluded.supports_images,
                   supports_reasoning=excluded.supports_reasoning, supports_tools=excluded.supports_tools,
                   context_window=excluded.context_window, max_output_tokens=excluded.max_output_tokens""",
            ("mimo", m["model_id"], m["display_name"], m["supports_images"],
             m["supports_reasoning"], m["supports_tools"], m["context_window"],
             m["max_output_tokens"], order),
        )

    # vLLM provider shell (user fills base_url)
    conn.execute(
        """INSERT INTO providers (id, type, display_name, base_url, api_key, config_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name""",
        ("vllm", "vllm", "Qwen (vLLM)", "", None, json.dumps({"enable_thinking": False}), now),
    )

    # Remove stale builtin models
    keep = {m["model_id"] for m in MIMO_MODELS}
    conn.execute(
        "DELETE FROM models WHERE provider_id='mimo' AND model_id NOT IN ({})".format(
            ",".join("?" for _ in keep)
        ),
        list(keep),
    )


# ── Provider CRUD ──


def list_providers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM providers ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def get_provider(conn: sqlite3.Connection, provider_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    return dict(row) if row else None


def create_provider(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    now = int(time.time())
    conn.execute(
        "INSERT INTO providers (id, type, display_name, base_url, api_key, config_json, created_at) VALUES (?,?,?,?,?,?,?)",
        (data["id"], data["type"], data["display_name"], data.get("base_url", ""),
         data.get("api_key"), json.dumps(data.get("config", {})), now),
    )
    conn.commit()
    return get_provider(conn, data["id"])  # type: ignore


def update_provider(conn: sqlite3.Connection, provider_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    sets = []
    vals = []
    for col in ("display_name", "base_url", "api_key"):
        if col in data:
            sets.append(f"{col}=?")
            vals.append(data[col])
    if "config" in data:
        sets.append("config_json=?")
        vals.append(json.dumps(data["config"]))
    if not sets:
        return get_provider(conn, provider_id)
    vals.append(provider_id)
    conn.execute(f"UPDATE providers SET {','.join(sets)} WHERE id=?", vals)
    conn.commit()
    return get_provider(conn, provider_id)


def delete_provider(conn: sqlite3.Connection, provider_id: str) -> bool:
    cur = conn.execute("DELETE FROM providers WHERE id=?", (provider_id,))
    conn.commit()
    return cur.rowcount > 0


# ── Model CRUD ──


def list_models(conn: sqlite3.Connection, provider_id: str | None = None) -> list[dict[str, Any]]:
    if provider_id:
        rows = conn.execute(
            "SELECT * FROM models WHERE provider_id=? ORDER BY sort_order", (provider_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM models ORDER BY provider_id, sort_order").fetchall()
    return [dict(r) for r in rows]


def get_model(conn: sqlite3.Connection, model_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    return dict(row) if row else None


def create_model(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    cur = conn.execute(
        """INSERT INTO models (provider_id, model_id, display_name, supports_images, supports_reasoning,
               supports_tools, context_window, max_output_tokens, sort_order)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (data["provider_id"], data["model_id"], data.get("display_name"),
         data.get("supports_images", 0), data.get("supports_reasoning", 0),
         data.get("supports_tools", 0), data.get("context_window"),
         data.get("max_output_tokens"), data.get("sort_order", 0)),
    )
    conn.commit()
    return get_model(conn, cur.lastrowid)  # type: ignore


def update_model(conn: sqlite3.Connection, model_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    sets = []
    vals = []
    for col in ("display_name", "supports_images", "supports_reasoning", "supports_tools",
                "context_window", "max_output_tokens", "sort_order"):
        if col in data:
            sets.append(f"{col}=?")
            vals.append(data[col])
    if not sets:
        return get_model(conn, model_id)
    vals.append(model_id)
    conn.execute(f"UPDATE models SET {','.join(sets)} WHERE id=?", vals)
    conn.commit()
    return get_model(conn, model_id)


def delete_model(conn: sqlite3.Connection, model_id: int) -> bool:
    cur = conn.execute("DELETE FROM models WHERE id=?", (model_id,))
    conn.commit()
    return cur.rowcount > 0


# ── Chat Logs ──


def insert_chat_log(conn: sqlite3.Connection, entry: dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO chat_logs (ts, provider_id, model, request_model, upstream_model, status_code, duration_ms,
               prompt_tokens, completion_tokens, stream, error_snippet, request_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (entry.get("ts", int(time.time())), entry.get("provider_id"), entry.get("model"),
         entry.get("request_model"), entry.get("upstream_model"), entry.get("status_code"), entry.get("duration_ms"),
         entry.get("prompt_tokens"), entry.get("completion_tokens"), entry.get("stream", 0),
         entry.get("error_snippet"), entry.get("request_id")),
    )
    conn.commit()


def list_chat_logs(conn: sqlite3.Connection, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM chat_logs ORDER BY ts DESC LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()
    return [dict(r) for r in rows]


def get_log_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """SELECT COUNT(*) as total,
                  COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                  COALESCE(SUM(completion_tokens), 0) as total_completion_tokens,
                  COALESCE(SUM(duration_ms), 0) as total_duration_ms
           FROM chat_logs"""
    ).fetchone()
    return dict(row)


# ── Settings ──


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


# ── Log Cleanup ──


def cleanup_logs(conn: sqlite3.Connection, max_rows: int = 1000, max_age_seconds: int = 604800) -> int:
    """Delete oldest logs beyond max_rows and older than max_age. Returns rows deleted."""
    cutoff = int(time.time()) - max_age_seconds
    total = 0
    cur = conn.execute(
        "DELETE FROM chat_logs WHERE id NOT IN (SELECT id FROM chat_logs ORDER BY ts DESC LIMIT ?)",
        (max_rows,),
    )
    total += cur.rowcount
    cur = conn.execute("DELETE FROM chat_logs WHERE ts < ?", (cutoff,))
    total += cur.rowcount
    if total > 0:
        conn.commit()
        log.debug(f"Cleaned up {total} log entries")
    return total
