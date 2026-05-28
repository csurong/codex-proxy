"""Entry point: launch FastAPI server with uvicorn."""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser

from .config import load_config
from .db import cleanup_logs
from .log import setup_logging, get_logger


def run_log_cleanup(conn, interval: int = 900, max_rows: int = 1000, max_age: int = 604800):
    """Background thread: cleanup logs every interval seconds."""
    while True:
        time.sleep(interval)
        try:
            cleanup_logs(conn, max_rows, max_age)
        except Exception:
            pass


def main():
    cfg = load_config()
    setup_logging(cfg.verbose)
    log = get_logger()

    # Initialize app (sets up DB)
    from .app import app, init_app, mount_static
    init_app(cfg)
    mount_static()

    # Start log cleanup background thread
    from .app import _get_conn
    cleanup_thread = threading.Thread(
        target=run_log_cleanup,
        args=(_get_conn(), 900, cfg.log_max_rows, cfg.log_max_age_days * 86400),
        daemon=True,
    )
    cleanup_thread.start()

    # Auto-open browser
    if cfg.open_browser and not os.environ.get("CODEX_PROXY_NO_BROWSER"):
        def _open():
            time.sleep(1.5)
            webbrowser.open(f"http://{cfg.host}:{cfg.port}/admin/")
        threading.Thread(target=_open, daemon=True).start()

    log.info(f"Codex-Proxy starting on http://{cfg.host}:{cfg.port}")
    log.info(f"Admin UI: http://{cfg.host}:{cfg.port}/admin/")
    log.info(f"Proxy endpoint: http://{cfg.host}:{cfg.port}/v1/responses")

    import uvicorn
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
