"""PyInstaller entrypoint for the Codex-Proxy desktop sidecar."""

from __future__ import annotations

import os
import sys


def ensure_standard_streams() -> None:
    """Provide safe stdio handles for PyInstaller --noconsole builds."""
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


if __name__ == "__main__":
    ensure_standard_streams()
    from codex_proxy.main import main

    main()
