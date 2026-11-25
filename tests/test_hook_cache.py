"""Hooks share one verdict: parallel tool calls cost one check, a fresh one costs no Python."""

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from claude_geo_guard import checker, config, installer, monitor, verdict_cache
from claude_geo_guard.checker import Verdict
from claude_geo_guard.platforms.linux import processes
from claude_geo_guard.storage import atomic_write, json_bytes

APP = processes.Process(10, 1, 1, "/opt/claude")
ALLOWED = Verdict(True, "verified", {4: "GB"}, "GB")
MISMATCH = Verdict(False, "egress outside configured country GB", {4: "FR"}, "GB")
COUNTING = """
from pathlib import Path as _Path
_here = _Path(__file__).parent
with open(_here / "calls", "a") as _log:
    _log.write("call\\n")
if (_here / "DELAY").exists():
    import time as _time
    _time.sleep(float((_here / "DELAY").read_text()))
"""


class Guard:
    def __init__(self, paths, bins):
        self.paths, self.bins = paths, bins
        self.command = paths.bin / "claude-geo-guard"

    def calls(self):
        log = self.bins / "calls"
        return len(log.read_text().splitlines()) if log.exists() else 0

    def reset(self):
        (self.bins / "calls").unlink(missing_ok=True)

    def hook(self, event=None, **env):
        payload = json.dumps({"hook_event_name": event}) if event else ""
        return subprocess.run(
            [self.command, "hook"],
            input=payload,
            capture_output=True,
            text=True,
            env={**os.environ, **env},
        )


@pytest.fixture
def guard(environment):
    paths, bins = environment["paths"], environment["bins"]
    installer.install(paths, installer.preflight(paths, skip_desktop=True))
    curl = bins / "curl"
    shebang, body = curl.read_text().split("\n", 1)
    curl.write_text(shebang + COUNTING + body)
    return Guard(paths, bins)


def test_parallel_tool_calls_share_one_check(guard):
    subprocess.run([guard.command, "check"], check=True, capture_output=True)
    per_check = guard.calls()
    guard.reset()
    (guard.bins / "DELAY").write_text("1")
    with ThreadPoolExecutor(5) as pool:
        results = list(pool.map(lambda _: guard.hook("PreToolUse"), range(5)))
    assert [(r.returncode, r.stdout, r.stderr) for r in results] == [(0, "", "")] * 5
    assert guard.calls() == per_check


def test_fresh_verdict_answers_without_python(guard):
    assert guard.hook().returncode == 0
    guard.reset()
    # Any Python start now fails visibly; a disposable install, never the user's.
    (guard.paths.root / "lib/bootstrap.py").write_text("raise SystemExit(9)\n")
    started = time.perf_counter()
    for _ in range(20):
        assert guard.hook("PreToolUse").returncode == 0
    per_call = (time.perf_counter() - started) / 20
    assert guard.calls() == 0
    assert per_call < 0.05, f"fast path took {per_call * 1000:.1f} ms"
    # These must reach the full hook instead.
    assert guard.hook(HTTPS_PROXY="http://localhost:1").returncode == 9
    assert guard.hook(CURSOR_VERSION="3.0.0").returncode == 9
    future = time.time() + 2
    os.utime(guard.paths.guard_config, (future, future))  # A policy edit after the check.
    assert guard.hook().returncode == 9


def test_stale_verdict_reaches_the_full_hook(guard):
    assert guard.hook().returncode == 0
    guard.paths.verdict.write_text(f"{int(time.time()) - 1} allow\n")
    (guard.paths.root / "lib/bootstrap.py").write_text("raise SystemExit(9)\n")
    assert guard.hook().returncode == 9


def test_unverifiable_network_never_blocks(guard):
    (guard.bins / "FAKE_CURL_EXIT").write_text("28")
    first = guard.hook("PreToolUse")
    assert (first.returncode, first.stdout, first.stderr) == (0, "", "")
    calls = guard.calls()
    assert guard.hook("PreToolUse").returncode == 0
    assert guard.calls() == calls  # The outage is checked once, not per tool call.


def test_denial_stops_the_turn_without_telling_the_model(guard):
    (guard.bins / "FAKE_COUNTRY").write_text("FR")
    tool = guard.hook("PreToolUse")
    assert tool.returncode == 0 and not tool.stderr
    response = json.loads(tool.stdout)
    # A tool denial reason would be fed to the model; a stop reason is shown to the user only.
    assert set(response) == {"continue", "stopReason"} and response["continue"] is False
    assert "detected FR" in response["stopReason"]
    assert "back in GB" in response["stopReason"]
    prompt = guard.hook("UserPromptSubmit")
    assert prompt.returncode == 2 and not prompt.stdout
    assert "detected FR" in prompt.stderr
    (guard.bins / "FAKE_COUNTRY").write_text("GB")
    assert guard.hook("UserPromptSubmit").returncode == 0  # A denial is never reused.


def test_monitor_publishes_until_the_next_check_and_clears_on_route_change(isolated):
    atomic_write(isolated.guard_config, json_bytes(config.DEFAULTS))
    share = monitor._sharer(isolated, 15)
    share(ALLOWED)
    kind, _message, _written, expires = verdict_cache.read(isolated)
    assert kind == "allow"
    assert expires >= time.time() + 15 + verdict_cache.MONITOR_SLACK_SECONDS - 2
    share(MISMATCH)
    kind, message, _written, expires = verdict_cache.read(isolated)
    assert kind == "deny" and "detected FR" in message and expires <= time.time()

    calls = []
    connectivity = monitor.Connectivity(routes=lambda: SimpleNamespace(fingerprint="new"))
    connectivity.known = {"old"}
    monitor.tick(
        {"/opt/claude"},
        checker=lambda: ALLOWED,
        scanner=lambda binaries, previous: [APP],
        pauser=lambda found, binaries=(): [],
        resumer=lambda paused: None,
        killer=Mock(),
        notifier=Mock(),
        connectivity=connectivity,
        check_due=False,
        stop=Mock(wait=Mock(return_value=False)),
        share=calls.append,
    )
    assert calls == [None, ALLOWED]


def test_split_tunnel_endpoint_timeout_does_not_hide_a_verified_one(isolated, monkeypatch):
    atomic_write(isolated.guard_config, json_bytes(dict(config.DEFAULTS, split_tunnel=True)))
    patience = []

    def query(family, host, patient=False):
        patience.append(patient)
        if host == "api.anthropic.com":
            raise checker.ProviderFailure("IPv4: provider request timed out", transient=True)
        return checker.Egress(answer, "1.1.1.1")

    monkeypatch.setattr(checker, "query", query)
    routes = lambda: SimpleNamespace(families=(4,), fingerprint="test")  # noqa: E731
    answer = "GB"
    result = checker.check(routes=routes, environ={})
    assert result.allowed and "unreachable" in result.reason
    assert True in patience  # The retry gave the slow endpoint more time.
    answer = "FR"
    result = checker.check(routes=routes, environ={})
    assert not result.allowed and not result.transient


def test_no_verified_endpoint_stays_unverified(isolated, monkeypatch):
    def query(family, host, patient=False):
        raise checker.ProviderFailure("IPv4: provider request timed out", transient=True)

    monkeypatch.setattr(checker, "query", query)
    result = checker.check(
        routes=lambda: SimpleNamespace(families=(4,), fingerprint="test"), environ={}
    )
    assert not result.allowed and result.transient


def test_patient_query_allows_a_slow_connection_more_time(monkeypatch):
    run = Mock(
        return_value=subprocess.CompletedProcess([], 0, b"h=claude.ai\nip=1.1.1.1\nloc=GB\n200")
    )
    monkeypatch.setattr(subprocess, "run", run)
    checker.query(4, "claude.ai", patient=True)
    args = run.call_args.args[0]
    assert args[args.index("--connect-timeout") + 1] == "6"
    assert int(args[args.index("--max-time") + 1]) < run.call_args.kwargs["timeout"]


def test_missing_policy_invalidates_both_cache_paths(guard):
    assert guard.hook().returncode == 0
    guard.paths.guard_config.unlink()
    assert verdict_cache.read(guard.paths) is None
    result = guard.hook("UserPromptSubmit")
    assert result.returncode == 2
    assert "configuration is missing" in result.stderr


def test_general_egress_alone_cannot_verify_claude(isolated, monkeypatch):
    def query(family, host, patient=False):
        if host in checker.TRACE_HOSTS:
            raise checker.ProviderFailure("unreachable", transient=True)
        return checker.Egress("GB", "1.1.1.1")

    monkeypatch.setattr(checker, "query", query)
    result = checker.check(
        routes=lambda: SimpleNamespace(families=(4,), fingerprint="test"), environ={}
    )
    assert not result.allowed and result.transient
