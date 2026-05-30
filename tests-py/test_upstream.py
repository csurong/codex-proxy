"""Tests for upstream error handling."""

from codex_proxy.upstream import UpstreamError


def test_web_search_disabled_error_gets_actionable_message():
    err = UpstreamError(400, "raw", '{"error":"webSearchEnabled is false"}')

    assert "MiMo Web Search Plugin is not activated" in err.message
    assert "platform.xiaomimimo.com" in err.message
