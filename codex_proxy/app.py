"""FastAPI application: admin API + proxy endpoints."""

from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import Config
from .db import (
    open_db, list_providers, get_provider, create_provider, update_provider, delete_provider,
    list_models, get_model, create_model, update_model, delete_model,
    insert_chat_log, list_chat_logs, get_log_stats, get_setting, set_setting, cleanup_logs,
)
from .log import get_logger, setup_logging
from .providers import (
    ProviderRuntime, ModelMeta,
    normalize_body, find_provider_for_model, strip_images_for_non_vision,
    resolve_mimo_base_url,
)
from .translate import req_to_chat, resp_to_responses, stream_to_sse
from .types import ResponsesRequest
from .upstream import call_upstream, call_upstream_stream, UpstreamError
from .codex_config import update_codex_proxy_url, restore_codex_backup

log = get_logger()

# Global state
_db_conn = None
_config: Config | None = None
_proxy_running = False
_proxy_started_at: float | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_conn, _config
    _config = Config()  # Will be overridden by main.py
    yield
    if _db_conn:
        _db_conn.close()


app = FastAPI(title="Codex-Proxy", lifespan=lifespan)


def init_app(cfg: Config):
    """Initialize the app with config (called from main.py)."""
    global _db_conn, _config
    _config = cfg
    os.makedirs(cfg.data_dir, exist_ok=True)
    _db_conn = open_db(cfg.data_dir)


def _get_conn():
    global _db_conn
    if _db_conn is None:
        raise RuntimeError("Database not initialized")
    return _db_conn


def _get_cfg() -> Config:
    global _config
    if _config is None:
        return Config()
    return _config


# ── Admin API: Proxy control ──


@app.get("/admin/api/status")
async def proxy_status():
    return {
        "running": _proxy_running,
        "port": _get_cfg().port,
        "uptime": int(time.time() - _proxy_started_at) if _proxy_started_at and _proxy_running else 0,
    }


@app.post("/admin/api/proxy/start")
async def proxy_start():
    global _proxy_running, _proxy_started_at
    _proxy_running = True
    _proxy_started_at = time.time()
    cfg = _get_cfg()
    proxy_url = f"http://{cfg.host}:{cfg.port}/v1"
    try:
        codex_result = update_codex_proxy_url(proxy_url)
    except Exception as e:
        codex_result = {"changed": False, "message": f"Could not update Codex config: {e}"}
    return {"ok": True, "message": "Proxy started", "proxy_url": proxy_url, "codex_config": codex_result}


@app.post("/admin/api/proxy/stop")
async def proxy_stop():
    global _proxy_running
    _proxy_running = False
    return {"ok": True, "message": "Proxy stopped"}


# ── Admin API: Providers ──


@app.get("/admin/api/providers")
async def api_list_providers():
    conn = _get_conn()
    providers = list_providers(conn)
    # Attach model count
    for p in providers:
        p["model_count"] = len(list_models(conn, p["id"]))
        # Don't expose api_key in full
        if p.get("api_key"):
            p["api_key_preview"] = p["api_key"][:6] + "***"
        else:
            p["api_key_preview"] = None
    return providers


@app.post("/admin/api/providers")
async def api_create_provider(request: Request):
    data = await request.json()
    conn = _get_conn()
    # Generate ID
    ptype = data.get("type", "custom")
    if ptype == "mimo":
        pid = "mimo"
    elif ptype == "vllm":
        pid = "vllm"
    else:
        import nanoid
        pid = f"custom_{nanoid.generate(size=8)}"

    try:
        p = create_provider(conn, {
            "id": pid,
            "type": ptype,
            "display_name": data.get("display_name", ""),
            "base_url": data.get("base_url", ""),
            "api_key": data.get("api_key"),
            "config": data.get("config", {}),
        })
        return p
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.patch("/admin/api/providers/{provider_id}")
async def api_update_provider(provider_id: str, request: Request):
    data = await request.json()
    conn = _get_conn()
    p = update_provider(conn, provider_id, data)
    if not p:
        return JSONResponse({"error": "Provider not found"}, status_code=404)
    return p


@app.delete("/admin/api/providers/{provider_id}")
async def api_delete_provider(provider_id: str):
    conn = _get_conn()
    if provider_id in ("mimo", "vllm"):
        return JSONResponse({"error": "Cannot delete built-in provider"}, status_code=400)
    ok = delete_provider(conn, provider_id)
    if not ok:
        return JSONResponse({"error": "Provider not found"}, status_code=404)
    return {"ok": True}


# ── Admin API: Models ──


@app.get("/admin/api/models")
async def api_list_models(provider_id: str | None = None):
    conn = _get_conn()
    return list_models(conn, provider_id)


@app.post("/admin/api/models")
async def api_create_model(request: Request):
    data = await request.json()
    conn = _get_conn()
    try:
        m = create_model(conn, data)
        return m
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.patch("/admin/api/models/{model_id}")
async def api_update_model(model_id: int, request: Request):
    data = await request.json()
    conn = _get_conn()
    m = update_model(conn, model_id, data)
    if not m:
        return JSONResponse({"error": "Model not found"}, status_code=404)
    return m


@app.delete("/admin/api/models/{model_id}")
async def api_delete_model(model_id: int):
    conn = _get_conn()
    ok = delete_model(conn, model_id)
    if not ok:
        return JSONResponse({"error": "Model not found"}, status_code=404)
    return {"ok": True}


# ── Admin API: Logs ──


@app.get("/admin/api/logs")
async def api_list_logs(limit: int = 50, offset: int = 0):
    conn = _get_conn()
    return list_chat_logs(conn, limit, offset)


@app.get("/admin/api/logs/stats")
async def api_log_stats():
    conn = _get_conn()
    return get_log_stats(conn)


# ── Admin API: Settings ──


@app.get("/admin/api/settings")
async def api_get_settings():
    conn = _get_conn()
    # Return all known settings
    keys = ["log_max_rows", "log_max_age_days", "thinking_disabled", "thinking_force_high_effort", "active_model_id"]
    return {k: get_setting(conn, k) for k in keys}


@app.patch("/admin/api/settings")
async def api_update_settings(request: Request):
    data = await request.json()
    conn = _get_conn()
    for k, v in data.items():
        set_setting(conn, k, str(v))
    return {"ok": True}


# ── Admin API: Test connection ──


@app.post("/admin/api/test")
async def api_test_connection(request: Request):
    data = await request.json()
    base_url = data.get("base_url", "")
    api_key = data.get("api_key")
    model = data.get("model", "")

    if not base_url:
        return JSONResponse({"error": "base_url required"}, status_code=400)

    try:
        body = {"model": model or "test", "messages": [{"role": "user", "content": "hi"}], "max_completion_tokens": 5}
        resp = await call_upstream(base_url, api_key, body, timeout=10)
        return {"ok": True, "model": resp.model, "status": "connected"}
    except UpstreamError as e:
        return {"ok": False, "error": f"HTTP {e.status_code}: {e.message[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ── Proxy: /v1/responses ──


@app.post("/v1/responses")
async def proxy_responses(request: Request):
    """Core proxy endpoint: Responses API → upstream Chat Completions."""
    cfg = _get_cfg()
    conn = _get_conn()

    # Parse request
    try:
        body = await request.json()
        req = ResponsesRequest(**body)
    except Exception as e:
        return JSONResponse({"error": f"Invalid request: {e}"}, status_code=400)

    # Resolve provider
    providers_data = list_providers(conn)
    runtimes = []
    for p in providers_data:
        models_data = list_models(conn, p["id"])
        runtimes.append(ProviderRuntime.from_db_row(p, models_data))

    # Check for active model override
    active_model_id = get_setting(conn, "active_model_id")
    active_model = None
    if active_model_id:
        try:
            active_model = get_model(conn, int(active_model_id))
        except (ValueError, TypeError):
            active_model = None

    if active_model:
        # Route to the active model's provider regardless of req.model
        target_model_id = active_model["model_id"]
        try:
            runtime, model_meta = find_provider_for_model(runtimes, target_model_id)
        except ValueError:
            return JSONResponse({"error": f"Active model '{target_model_id}' not found in any provider"}, status_code=400)
    else:
        target_model_id = req.model
        try:
            runtime, model_meta = find_provider_for_model(runtimes, req.model)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=404)

    # Resolve base_url (MiMo auto-detect)
    base_url = runtime.base_url
    if runtime.type == "mimo":
        base_url = resolve_mimo_base_url(runtime.api_key, base_url)

    if not base_url:
        return JSONResponse({"error": f"No base_url configured for provider '{runtime.id}'"}, status_code=400)

    # Translate request
    chat_body = req_to_chat(req, cfg.expose_reasoning)
    # Override model in request body with the target model
    chat_body["model"] = target_model_id

    # Strip images for non-vision models
    if model_meta and not model_meta.supports_images:
        chat_body["messages"] = strip_images_for_non_vision(chat_body["messages"], model_meta)

    # Provider normalization
    chat_body = normalize_body(runtime, chat_body, target_model_id)

    start = time.time()
    log_entry: dict[str, Any] = {
        "provider_id": runtime.id,
        "model": req.model,
        "request_model": chat_body.get("model"),
        "stream": 1 if req.stream else 0,
    }

    try:
        if req.stream:
            return await _handle_stream(req, runtime, base_url, chat_body, cfg, start, log_entry)
        else:
            return await _handle_non_stream(req, runtime, base_url, chat_body, cfg, start, log_entry)
    except UpstreamError as e:
        log_entry["status_code"] = e.status_code
        log_entry["error_snippet"] = e.message[:500]
        log_entry["duration_ms"] = int((time.time() - start) * 1000)
        insert_chat_log(conn, log_entry)
        return JSONResponse(
            {"error": {"type": "upstream_error", "message": e.message[:500]}},
            status_code=e.status_code if e.status_code < 500 else 502,
        )
    except Exception as e:
        log_entry["status_code"] = 500
        log_entry["error_snippet"] = str(e)[:500]
        log_entry["duration_ms"] = int((time.time() - start) * 1000)
        insert_chat_log(conn, log_entry)
        log.exception("Proxy error")
        return JSONResponse(
            {"error": {"type": "proxy_error", "message": str(e)[:500]}},
            status_code=500,
        )


async def _handle_stream(req, runtime, base_url, chat_body, cfg, start, log_entry):
    chunks = call_upstream_stream(base_url, runtime.api_key, chat_body)

    # Peek at first chunk to capture upstream model name
    first_chunk_model = None
    async def _peek_model(it):
        nonlocal first_chunk_model
        async for chunk in it:
            if first_chunk_model is None and chunk.model:
                first_chunk_model = chunk.model
            yield chunk
    peeked_chunks = _peek_model(chunks)

    async def generate():
        async for sse_line in stream_to_sse(peeked_chunks, req, cfg.expose_reasoning):
            yield sse_line

    # Log after stream completes (we can't easily get usage from streaming)
    log_entry["status_code"] = 200
    log_entry["duration_ms"] = int((time.time() - start) * 1000)
    log_entry["upstream_model"] = first_chunk_model
    insert_chat_log(_get_conn(), log_entry)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _handle_non_stream(req, runtime, base_url, chat_body, cfg, start, log_entry):
    resp = await call_upstream(base_url, runtime.api_key, chat_body)
    result = resp_to_responses(resp, req, cfg.expose_reasoning)

    log_entry["status_code"] = 200
    log_entry["duration_ms"] = int((time.time() - start) * 1000)
    log_entry["upstream_model"] = resp.model or None
    if resp.usage:
        log_entry["prompt_tokens"] = resp.usage.prompt_tokens
        log_entry["completion_tokens"] = resp.usage.completion_tokens
    insert_chat_log(_get_conn(), log_entry)

    return JSONResponse(result.model_dump())


# ── Proxy: /v1/models ──


@app.get("/v1/models")
async def proxy_models():
    conn = _get_conn()
    providers_data = list_providers(conn)
    models = []
    for p in providers_data:
        for m in list_models(conn, p["id"]):
            models.append({
                "id": m["model_id"],
                "object": "model",
                "created": m.get("created_at", 0),
                "owned_by": p["id"],
            })
    return {"object": "list", "data": models}



@app.post("/admin/api/codex/restore")
async def api_restore_codex():
    return restore_codex_backup()

# ── Static files (admin UI) ──


def mount_static():
    """Mount static file serving for the admin UI. Call after all routes."""
    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.is_dir():
        app.mount("/admin", StaticFiles(directory=str(static_dir), html=True), name="admin")
    else:
        # Fallback: serve a minimal page
        @app.get("/admin")
        @app.get("/admin/{path:path}")
        async def admin_fallback(path: str = ""):
            return HTMLResponse(
                "<html><body><h1>Codex-Proxy</h1>"
                "<p>Admin UI not built yet. Run <code>npm run build</code> first.</p>"
                "<p>API available at <a href='/admin/api/status'>/admin/api/status</a></p>"
                "</body></html>"
            )

