"""Offline tolerance preserves running sessions; confirmed mismatches still close them."""

import json
import signal
import subprocess
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from claude_geo_guard import monitor
from claude_geo_guard.checker import Egress, Verdict, check
from claude_geo_guard.platforms import Unroutable
from claude_geo_guard.platforms.linux import networking, processes
from claude_geo_guard.platforms.linux.networking import NetworkState

APP = processes.Process(10, 1, 1, "/opt/claude")
ALLOWED = Verdict(True, "verified", {4: "GB"}, "GB", fingerprint="home")
TIMEOUT = Verdict(False, "timed out", {}, "GB", errors={4: "timed out"}, transient=True)
OFFLINE = Verdict(False, "no usable public routes", {}, "GB", transient=True)
MISMATCH = Verdict(False, "egress outside configured country GB", {4: "FR"}, "GB")


def same(fingerprint):
    return lambda: NetworkState((4,), fingerprint)


def run_tick(verdicts, connectivity, **kwargs):
    killer = Mock()
    sequence = iter(verdicts)
    kwargs.setdefault("pauser", Mock(side_effect=lambda found, binaries=(): list(found)))
    kwargs.setdefault("resumer", Mock())
    result = monitor.tick(
        {"/opt/claude"},
        checker=lambda: next(sequence),
        scanner=lambda binaries, previous: [APP],
        killer=killer,
        notifier=Mock(),
        connectivity=connectivity,
        stop=Mock(wait=Mock(return_value=False)),
        **kwargs,
    )
    return result, killer, kwargs


@pytest.mark.parametrize(
    "error, transient",
    [
        (TimeoutError(), True),
        (subprocess.TimeoutExpired("curl", 6), True),
        (ValueError("bad"), False),
    ],
)
def test_checker_marks_only_evidence_free_failures_transient(error, transient):
    verdict = check(routes=same("a"), request=Mock(side_effect=error), environ={})
    assert not verdict.allowed and verdict.transient is transient


def test_mismatch_alongside_a_timeout_is_not_transient():
    def request(family):
        if family == 6:
            raise TimeoutError()
        return Egress("FR", "1.1.1.1")

    verdict = check(routes=lambda: NetworkState((4, 6), "a"), request=request, environ={})
    assert not verdict.allowed and not verdict.transient


def test_blocked_or_missing_routes_are_transient_and_allowed_carries_fingerprint():
    def blocked():
        raise Unroutable("public routes are blocked")

    request = Mock(return_value=Egress("GB", "1.1.1.1"))
    assert check(routes=blocked, request=request, environ={}).transient
    assert check(routes=lambda: NetworkState((), "none"), request=request, environ={}).transient
    assert check(routes=same("a"), request=request, environ={}).fingerprint == "a"


def test_network_change_with_mismatch_is_not_transient():
    states = iter([NetworkState((4,), "a"), NetworkState((4,), "b")])
    verdict = check(
        routes=lambda: next(states), request=lambda f: Egress("FR", "1.1.1.1"), environ={}
    )
    assert not verdict.allowed and not verdict.transient


def snapshot_with(route_types, monkeypatch):
    def run(args, **kwargs):
        if "get" in args:
            family = 6 if "-6" in args else 4
            route = {"type": route_types[family], "dst": "x"}
            if route_types[family] == "unicast":
                route["dev"] = "wg0"
            return subprocess.CompletedProcess(args, 0, json.dumps([route]), "")
        return subprocess.CompletedProcess(args, 0, "[]", "")

    monkeypatch.setattr(subprocess, "run", run)
    return networking.snapshot


def test_kill_switch_is_unroutable_only_when_every_family_is_blocked(monkeypatch):
    with pytest.raises(Unroutable):
        snapshot_with({4: "blackhole", 6: "prohibit"}, monkeypatch)()
    with pytest.raises(RuntimeError) as error:
        snapshot_with({4: "unicast", 6: "blackhole"}, monkeypatch)()
    assert not isinstance(error.value, Unroutable)


def test_timeouts_recheck_quickly_then_enter_offline_mode_without_closing():
    announce = Mock()
    connectivity = monitor.Connectivity(routes=same("home"), announce=announce)
    delays = []
    for _ in range(4):
        result, killer, _ = run_tick([TIMEOUT], connectivity)
        assert result == [APP] and not killer.called
        delays.append(connectivity.delay(30))
    assert delays == [1, 3, 30, 30]
    assert connectivity.offline
    announce.assert_called_once_with(monitor.OFFLINE_MESSAGE)
    run_tick([ALLOWED], connectivity)
    assert not connectivity.offline and connectivity.failures == 0
    assert announce.call_args.args == (monitor.ONLINE_MESSAGE,)


def test_no_route_enters_offline_mode_immediately():
    connectivity = monitor.Connectivity(routes=same("home"))
    run_tick([OFFLINE], connectivity)
    assert connectivity.offline


def test_mismatch_closes_claude_even_while_offline():
    connectivity = monitor.Connectivity(routes=same("home"))
    connectivity.offline, connectivity.failures = True, 3
    _, killer, _ = run_tick([MISMATCH], connectivity)
    killer.assert_called_once()


def test_without_connectivity_a_timeout_still_closes():
    _, killer, _ = run_tick([TIMEOUT], None)
    killer.assert_called_once()


def test_network_change_pauses_until_verified():
    connectivity = monitor.Connectivity(routes=same("cafe"))
    connectivity.allowed(ALLOWED)
    verified = Verdict(True, "verified", {4: "GB"}, "GB", fingerprint="cafe")
    _, killer, calls = run_tick([TIMEOUT, verified], connectivity, check_due=False)
    calls["pauser"].assert_called_once_with([APP], {"/opt/claude"})
    calls["resumer"].assert_called_once_with([APP])
    assert not killer.called and connectivity.known == {"cafe"}
    # The verified network does not pause again, and an unscheduled event does no check.
    result, _, calls = run_tick([], connectivity, check_due=False)
    assert result == [APP] and not calls["pauser"].called


def test_network_change_to_wrong_country_closes_while_paused():
    connectivity = monitor.Connectivity(routes=same("proxy"))
    connectivity.allowed(ALLOWED)
    _, killer, calls = run_tick([MISMATCH], connectivity, check_due=False)
    calls["pauser"].assert_called_once()
    killer.assert_called_once()
    calls["resumer"].assert_called_once()


def test_unreachable_new_network_is_tolerated_after_paused_rechecks():
    connectivity = monitor.Connectivity(routes=same("jungle"))
    connectivity.allowed(ALLOWED)
    _, killer, calls = run_tick([TIMEOUT] * 3, connectivity, check_due=False)
    assert not killer.called and connectivity.offline
    assert "jungle" in connectivity.known
    calls["resumer"].assert_called_once()
    _, _, calls = run_tick([], connectivity, check_due=False)
    assert not calls["pauser"].called


def test_blocked_network_has_a_stable_fingerprint():
    def blocked():
        raise Unroutable("blocked")

    connectivity = monitor.Connectivity(routes=blocked)
    connectivity.allowed(ALLOWED)
    run_tick([OFFLINE], connectivity, check_due=False)
    assert connectivity.offline and not connectivity.moved()


def state(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]


def test_pause_freezes_background_processes_and_terminate_reaches_them():
    child = subprocess.Popen(["/bin/sleep", "60"], start_new_session=True)
    try:
        found = processes.inspect(child.pid)
        assert found.tty == 0
        assert processes.pause([found]) == [found]
        deadline = time.monotonic() + 2
        while state(child.pid) != "T" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert state(child.pid) == "T"
        processes.resume([found])
        deadline = time.monotonic() + 2
        while state(child.pid) == "T" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert state(child.pid) != "T"
        processes.pause([found])
        processes.terminate([found], grace=2)
        assert child.wait(timeout=5) == -signal.SIGTERM
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_terminal_sessions_are_never_paused():
    terminal = processes.Process(1, 1, 0, "/usr/bin/claude", tty=34816)
    assert processes.pause([terminal]) == []


def test_wake_reports_network_events_but_not_timeouts():
    wake = monitor.Wake()
    assert wake.sleep(0) is False
    wake.poke()
    assert wake.sleep(5) is True
    wake.poke()
    wake.halt()
    assert wake.sleep(5) is False and wake.stop.is_set()


def test_route_watcher_wakes_the_monitor(environment):
    events = []
    fake = environment["bins"] / "ip"
    original = fake.read_text()
    fake.write_text(original.replace("time.sleep(3600)", 'print("route change", flush=True)'))
    watcher = networking.Watcher(lambda: events.append(1))
    watcher.thread.join(timeout=5)
    watcher.close()
    assert events == [1]
