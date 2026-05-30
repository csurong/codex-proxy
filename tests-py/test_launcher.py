"""Tests for the PyInstaller sidecar launcher."""

import sys

from codex_proxy_launcher import ensure_standard_streams


def test_ensure_standard_streams_replaces_missing_streams(monkeypatch):
    monkeypatch.setattr(sys, "stdin", None)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    ensure_standard_streams()

    assert sys.stdin is not None
    assert sys.stdout is not None
    assert sys.stderr is not None
    assert hasattr(sys.stderr, "isatty")
