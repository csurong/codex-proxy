"""FastAPI application: admin API + proxy endpoints."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import Config, load_config
from .db import (
    open_db, list_providers, get_provider, create_provider, update_provider, delete_provider,
    list_models, get_model, create_model, update_model, delete_model,
    insert_chat_log, list_chat_logs, get_log_stats, get_setting, set_setting, cleanup_logs,
)
from .log import get_logger, setup_logging
from .providers import (
    ProviderRuntime, ModelMeta,
    normalize_body, resolve_provider_for_model, strip_images_for_non_vision,
    resolve_mimo_base_url, is_mimo_token_plan, body_has_images, find_image_model,
)
from .translate import req_to_chat, resp_to_responses, stream_to_sse
from .types import ResponsesRequest
from .upstream import call_upstream, call_upstream_stream, UpstreamError
from .codex_config import apply_codex_config, restore_codex_backup

log = get_logger()

# Global state
_db_conn = None
_config: Config | None = None
_proxy_running = False
_proxy_started_at: float | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_conn, _config
    if _db_conn is None:
        init_app(load_config())
    mount_static()
    yield
    if _db_conn:
        _db_conn.close()


app = FastAPI(title="Codex-Proxy", lifespan=lifespan)


def init_app(cfg: Config):
    """Initialize the app with config (called from main.py)."""
    global _db_conn, _config, _proxy_running, _proxy_started_at
    _config = cfg
    _proxy_running = False
    _proxy_started_at = None
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


def _public_provider(p: dict[str, Any]) -> dict[str, Any]:
    """Return provider data safe for admin API responses."""
    p = dict(p)
    api_key = p.pop("api_key", None)
    p["api_key_preview"] = api_key[:6] + "***" if api_key else None
    return p


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
        conn = _get_conn()
        provider = get_provider(conn, "mimo")
        model = None
        active_model_id = get_setting(conn, "active_model_id")
        if active_model_id:
            try:
                active_model = get_model(conn, int(active_model_id))
                if active_model:
                    model = active_model
                    provider = get_provider(conn, active_model["provider_id"])
            except (ValueError, TypeError):
                pass
        if model is None:
            models = list_models(conn, provider["id"] if provider else "mimo")
            model = models[0] if models else None
        if provider and model:
            codex_result = apply_codex_config(
                proxy_url,
                provider_key=provider["id"],
                provider_name=provider["display_name"],
                model_id=model["model_id"],
                context_window=model.get("context_window"),
                max_output_tokens=model.get("max_output_tokens"),
                supports_reasoning=bool(model.get("supports_reasoning")),
            )
        else:
            codex_result = {"changed": False, "message": "No provider/model available for Codex config"}
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
    result = []
    for p in providers:
        p["model_count"] = len(list_models(conn, p["id"]))
        result.append(_public_provider(p))
    return result


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
        return _public_provider(p)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.patch("/admin/api/providers/{provider_id}")
async def api_update_provider(provider_id: str, request: Request):
    data = await request.json()
    conn = _get_conn()
    if data.get("api_key") == "":
        data = {k: v for k, v in data.items() if k != "api_key"}
    p = update_provider(conn, provider_id, data)
    if not p:
        return JSONResponse({"error": "Provider not found"}, status_code=404)
    return _public_provider(p)


@app.post("/admin/api/providers/{provider_id}/test")
async def api_test_provider(provider_id: str):
    conn = _get_conn()
    provider = get_provider(conn, provider_id)
    if not provider:
        return JSONResponse({"ok": False, "error": "Provider not found"}, status_code=404)

    runtime = ProviderRuntime.from_db_row(provider, list_models(conn, provider_id))
    base_url = runtime.base_url
    if runtime.type == "mimo":
        base_url = resolve_mimo_base_url(runtime.api_key, base_url)
    if not base_url:
        return JSONResponse({"ok": False, "error": "base_url required"}, status_code=400)

    model = runtime.models[0].model_id if runtime.models else "test"
    try:
        body = {"model": model, "messages": [{"role": "user", "content": "hi"}], "max_completion_tokens": 5}
        resp = await call_upstream(base_url, runtime.api_key, body, timeout=10)
        return {"ok": True, "model": resp.model or model, "status": "connected"}
    except UpstreamError as e:
        return {"ok": False, "error": f"HTTP {e.status_code}: {e.message[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


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

    if not _proxy_running:
        return JSONResponse({"error": "Proxy is stopped"}, status_code=503)

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
        runtime = next((p for p in runtimes if p.id == active_model["provider_id"]), None)
        if not runtime:
            return JSONResponse(
                {"error": f"Active model provider '{active_model['provider_id']}' not found"},
                status_code=400,
            )
        model_meta = next((m for m in runtime.models if m.model_id == target_model_id), None)
    else:
        try:
            selection = resolve_provider_for_model(runtimes, req.model)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        runtime = selection.provider
        model_meta = selection.model_meta
        target_model_id = selection.model_id

    # Resolve base_url (MiMo auto-detect)
    base_url = runtime.base_url
    if runtime.type == "mimo":
        base_url = resolve_mimo_base_url(runtime.api_key, base_url)

    if not base_url:
        return JSONResponse({"error": f"No base_url configured for provider '{runtime.id}'"}, status_code=400)

    # Read thinking settings
    thinking_disabled = get_setting(conn, "thinking_disabled") == "1"
    force_high_effort = get_setting(conn, "thinking_force_high_effort") == "1"

    # Translate request
    chat_body = req_to_chat(
        req,
        expose_reasoning=cfg.expose_reasoning,
        stream=req.stream,
        thinking_disabled=thinking_disabled,
        force_high_effort=force_high_effort,
        enable_web_search=runtime.type == "mimo" and not is_mimo_token_plan(runtime.api_key, base_url),
    )

    if body_has_images(chat_body) and (not model_meta or not model_meta.supports_images):
        image_model = find_image_model(runtime)
        if image_model:
            target_model_id = image_model.model_id
            model_meta = image_model

    # Override model in request body with the target model
    chat_body["model"] = target_model_id

    # Strip images for non-vision models
    if model_meta and not model_meta.supports_images:
        chat_body["messages"] = strip_images_for_non_vision(chat_body["messages"], model_meta)

    # Provider normalization
    chat_body = normalize_body(runtime, chat_body, target_model_id, thinking_on=False if thinking_disabled else None)

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

    stream_usage: dict[str, int] = {}

    async def generate():
        keepalive_interval = 15.0
        queue: asyncio.Queue[tuple[str, str | BaseException | None]] = asyncio.Queue()

        async def produce():
            try:
                async for sse_line in stream_to_sse(peeked_chunks, req, cfg.expose_reasoning, usage_out=stream_usage):
                    await queue.put(("event", sse_line))
            except Exception as e:
                await queue.put(("error", e))
            finally:
                await queue.put(("done", None))

        producer = asyncio.create_task(produce())

        try:
            while True:
                try:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=keepalive_interval)
                except asyncio.TimeoutError:
                    # Send keepalive comment without cancelling the upstream stream.
                    yield ": keepalive\n\n"
                    continue

                if kind == "event":
                    yield payload  # type: ignore[misc]
                    continue
                if kind == "error":
                    raise payload  # type: ignore[misc]
                break
        finally:
            if not producer.done():
                producer.cancel()
                with suppress(asyncio.CancelledError):
                    await producer

        if producer.done() and not producer.cancelled():
            try:
                producer.result()
            except Exception:
                raise

        # Log after stream is fully consumed by the client
        log_entry["status_code"] = 200
        log_entry["duration_ms"] = int((time.time() - start) * 1000)
        log_entry["upstream_model"] = first_chunk_model
        if stream_usage:
            log_entry["prompt_tokens"] = stream_usage.get("prompt_tokens")
            log_entry["completion_tokens"] = stream_usage.get("completion_tokens")
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
    if any(getattr(route, "path", None) in ("/admin", "/admin/{path:path}") for route in app.routes):
        return
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent))
    static_dir = bundle_root / "static"
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
