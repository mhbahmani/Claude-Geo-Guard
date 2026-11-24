from claude_geo_guard.platforms.linux import dialogs
import signal
import json
import logging
import threading
from unittest.mock import Mock

import pytest

from claude_geo_guard import monitor
from claude_geo_guard.checker import Verdict
from claude_geo_guard.platforms.linux.processes import Process
from claude_geo_guard.storage import atomic_write, json_bytes

P = Process(42, 100, 1, "/opt/Claude")


def test_idle_no_provider_requests():
    checker = Mock()
    assert monitor.tick(set(), scanner=lambda *_: [], checker=checker) == []
    checker.assert_not_called()


@pytest.mark.parametrize("allowed", [True, False])
def test_running_policy(allowed):
    killer = Mock()
    notifier = Mock()

    def checker():
        return Verdict(allowed, "test", {})

    result = monitor.tick(
        {P.executable}, scanner=lambda *_: [P], checker=checker, killer=killer, notifier=notifier
    )
    assert result == [P]
    assert killer.called == (not allowed)
    assert notifier.called == (not allowed)


def test_country_transitions():
    checks = iter([True, False, True, False])
    killer = Mock()
    previous = []
    for _ in range(4):
        previous = monitor.tick(
            {P.executable},
            previous,
            scanner=lambda *_: [P],
            checker=lambda: Verdict(next(checks), "transition", {}),
            killer=killer,
            notifier=Mock(),
        )
    assert killer.call_count == 2


def test_exits_during_check():
    scanner = Mock(side_effect=[[P], []])
    killer = Mock()
    monitor.tick(
        set(),
        scanner=scanner,
        checker=lambda: Verdict(False, "offline", {}),
        killer=killer,
        notifier=Mock(),
    )
    killer.assert_not_called()


def test_dialog_failure_does_not_break_monitor(monkeypatch, caplog):
    monkeypatch.setattr(dialogs, "show_error", Mock(side_effect=FileNotFoundError()))
    alerts = monitor.RuntimeAlerts()
    try:
        alerts(Verdict(False, "offline", {}))
        alerts.worker.join(timeout=2)
        assert not alerts.lock.locked()
        assert "runtime_dialog_failed" in caplog.text
    finally:
        alerts.close()


def test_open_window_does_not_pause_checks_or_spawn_more_windows(monkeypatch, caplog):
    opened = threading.Event()
    dismiss = threading.Event()

    def show(error, **kwargs):
        assert kwargs["runtime"]
        assert error.verdict.reason == "offline"
        opened.set()
        assert dismiss.wait(timeout=3)
        return True

    helper = Mock(side_effect=show)
    monkeypatch.setattr(dialogs, "show_error", helper)
    alerts = monitor.RuntimeAlerts()
    killer = Mock()
    try:
        for _ in range(2):
            monitor.tick(
                {P.executable},
                scanner=lambda *_: [P],
                checker=lambda: Verdict(False, "offline", {}),
                killer=killer,
                notifier=alerts,
            )
            assert opened.wait(timeout=1)
        assert killer.call_count == 2
        assert helper.call_count == 1
        dismiss.set()
        alerts.worker.join(timeout=2)
        alerts(Verdict(False, "offline", {}))
        alerts.worker.join(timeout=2)
        assert helper.call_count == 2
    finally:
        dismiss.set()
        alerts.close()


def test_runtime_logs_countries_reason_and_stop_order(caplog):
    caplog.set_level(logging.INFO)
    notifier = Mock()
    verdict = Verdict(
        False,
        "verification failed: IPv6: provider request timed out (curl exit 28)",
        {4: "GB"},
        "GB",
    )
    monitor.tick(
        {P.executable},
        scanner=lambda *_: [P],
        checker=lambda: verdict,
        killer=Mock(),
        notifier=notifier,
    )
    events = [json.loads(record.message) for record in caplog.records]
    assert [event["event"] for event in events] == [
        "runtime_check",
        "runtime_stop_requested",
        "runtime_stop_finished",
    ]
    assert events[0]["countries"] == {"4": "GB"}
    assert "curl exit 28" in events[1]["reason"]
    notifier.assert_called_once_with(verdict)


def test_clean_service_shutdown(isolated, monkeypatch):
    atomic_write(isolated.manifest, json_bytes({"binaries": []}))
    handlers = {}
    monkeypatch.setattr(signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    count = []

    def tick(*args, **kwargs):
        count.append(1)
        handlers[signal.SIGTERM]()
        return []

    monkeypatch.setattr(monitor, "tick", tick)
    monitor.run(isolated)
    assert count == [1]


def test_service_restart_rediscovers(isolated, monkeypatch):
    atomic_write(isolated.manifest, json_bytes({"binaries": [P.executable]}))
    handlers = {}
    monkeypatch.setattr(signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    observed = []

    def tick(binaries, previous, *args, **kwargs):
        observed.append((binaries, previous))
        handlers[signal.SIGTERM]()
        return [P]

    monkeypatch.setattr(monitor, "tick", tick)
    monitor.run(isolated)
    monitor.run(isolated)
    assert observed == [({P.executable}, []), ({P.executable}, [])]
