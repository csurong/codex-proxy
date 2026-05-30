"""Tests for process lifecycle helpers."""

import codex_proxy.main as main_module


def test_start_parent_watchdog_ignores_missing_parent_pid(monkeypatch):
    started = False

    class FakeThread:
        def __init__(self, *args, **kwargs):
            nonlocal started
            started = True

        def start(self):
            pass

    monkeypatch.setattr(main_module.threading, "Thread", FakeThread)

    main_module.start_parent_watchdog(None)
    main_module.start_parent_watchdog("")
    main_module.start_parent_watchdog("not-a-pid")

    assert started is False


def test_start_parent_watchdog_starts_for_numeric_parent_pid(monkeypatch):
    started = False

    class FakeThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            nonlocal started
            started = True

    monkeypatch.setattr(main_module.threading, "Thread", FakeThread)

    main_module.start_parent_watchdog("123")

    assert started is True
