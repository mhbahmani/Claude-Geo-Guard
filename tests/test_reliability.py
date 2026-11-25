"""Regressions for docs/plans/ubuntu-runtime-reliability.md."""

import os
import shutil
import subprocess
import time
from unittest.mock import Mock

import pytest

from claude_geo_guard import checker, config, desktop_launcher, executables, health, installer
from claude_geo_guard import monitor
from claude_geo_guard.checker import Egress, ProviderFailure, check, query
from claude_geo_guard.diagnostics import diagnostics
from claude_geo_guard.platforms.linux import desktop_entries, processes, systemd
from claude_geo_guard.platforms.linux.networking import NetworkState
from claude_geo_guard.storage import atomic_write, json_bytes


def native(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2("/bin/sleep", path)
    return path


def web_shortcut(environment, tmp_path):
    browser = native(tmp_path / "browser" / "chrome")
    entry = environment["entry"].with_name("chrome-abc-Default.desktop")
    entry.write_text(
        "[Desktop Entry]\nType=Application\nName=Claude\n"
        f'Exec="{browser}" --profile-directory=Default --app=https://claude.ai/new\n'
    )
    return browser, entry


def stop_after(cycles, between=None):
    waits = []

    class Stop:
        stopped = False

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, seconds):
            waits.append(seconds)
            if between:
                between(len(waits))
            if len(waits) >= cycles:
                self.set()
            return self.stopped

    return Stop, waits


# Issue 1: installed entry points keep their policy under a different environment.


def test_installed_hook_ignores_caller_xdg_roots(environment, tmp_path):
    paths = environment["paths"]
    installer.install(
        paths, installer.preflight(paths, country="DE", allowed_ips=["1.1.1.1", "2606:4700::1"])
    )
    guard = str(paths.bin / "claude-geo-guard")
    elsewhere = tmp_path / "elsewhere"
    env = dict(
        os.environ,
        XDG_DATA_HOME=str(elsewhere / "data"),
        XDG_CONFIG_HOME=str(elsewhere / "config"),
        XDG_STATE_HOME=str(elsewhere / "state"),
    )

    def hook():
        return subprocess.run([guard, "hook"], capture_output=True, text=True, env=env)

    assert hook().returncode == 2  # Provider reports GB; the installed policy is DE.
    (environment["bins"] / "FAKE_COUNTRY").write_text("DE")
    assert hook().returncode == 0
    atomic_write(paths.guard_config, json_bytes(dict(config.load(paths), allowed_ips=["8.8.8.8"])))
    assert hook().returncode == 2  # An atomic edit applies at the next check.
    paths.guard_config.unlink()
    denied = hook()
    assert denied.returncode == 2 and str(paths.guard_config) in denied.stderr
    assert not elsewhere.exists()


def test_installed_launcher_keeps_caller_environment_for_claude(environment, tmp_path):
    paths = environment["paths"]
    installer.install(paths, installer.preflight(paths, skip_desktop=True))
    script = environment["bins"] / "claude"
    script.write_text(
        "#!/usr/bin/python3\nimport os, sys\n"
        'print("2.1.295" if sys.argv[1:] == ["--version"] else os.environ["XDG_CONFIG_HOME"])\n'
    )
    custom = str(tmp_path / "custom-config")
    result = subprocess.run(
        [str(paths.bin / "claude")],
        capture_output=True,
        text=True,
        env=dict(os.environ, XDG_CONFIG_HOME=custom),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == custom


# Issue 3: a web shortcut never authorizes termination of its shared browser.


def test_web_shortcut_is_reported_and_never_registered(environment, tmp_path):
    paths = environment["paths"]
    browser, _entry = web_shortcut(environment, tmp_path)
    plan = installer.preflight(paths)
    assert str(browser) not in plan["binaries"]
    assert str(environment["desktop"]) in plan["binaries"]
    assert any("web shortcut" in reason for reason in plan["skipped_desktops"])
    assert installer.summary(plan)["unprotected_shortcuts"] == plan["skipped_desktops"]
    installer.install(paths, plan)
    shortcut = paths.data / "applications/chrome-abc-Default.desktop"
    assert not shortcut.exists()
    report = diagnostics(paths)
    assert any("web shortcut" in note for note in report["notes"])
    assert not any("chrome" in issue for issue in report["issues"])


def test_shared_named_executable_is_not_dedicated(environment, tmp_path):
    paths = environment["paths"]
    tool = native(tmp_path / "apps" / "launcher")
    environment["entry"].with_name("named.desktop").write_text(
        f'[Desktop Entry]\nType=Application\nName=Claude helper\nExec="{tool}" %U\n'
    )
    plan = installer.preflight(paths)
    assert str(tool) not in plan["binaries"]
    assert any("not a dedicated Claude executable" in r for r in plan["skipped_desktops"])


def test_legacy_shared_registration_is_released_and_ignored(
    environment, tmp_path, caplog, monkeypatch
):
    paths = environment["paths"]
    browser, entry = web_shortcut(environment, tmp_path)
    original = entry.read_text()
    # Reproduce a manifest written by a release without the identity check.
    with monkeypatch.context() as patch:
        patch.setattr(desktop_entries, "identify", lambda *_: None)
        installer.install(paths, installer.preflight(paths))
    legacy = installer.load_manifest(paths)
    legacy.pop("launchers")
    atomic_write(paths.manifest, json_bytes(legacy))
    override = paths.data / "applications" / entry.name
    assert str(browser) in legacy["binaries"] and override.exists()

    # Until reinstall, the monitor, launcher and doctor all refuse the browser.
    coverage = monitor.Coverage(paths)
    caplog.set_level("WARNING")
    coverage.refresh()
    assert str(browser) not in coverage.binaries
    assert str(environment["desktop"]) in coverage.binaries
    assert "registration_ignored" in caplog.text
    with pytest.raises(desktop_launcher.LaunchBlocked, match="shared with other applications"):
        desktop_launcher.resolve(legacy, str(browser))
    assert any("could close them" in issue for issue in diagnostics(paths)["issues"])

    app = subprocess.Popen([str(environment["desktop"]), "60"])
    other = subprocess.Popen([str(browser), "60"])
    try:
        selected = {p.pid for p in processes.scan(coverage.binaries)}
        assert app.pid in selected and other.pid not in selected
    finally:
        for child in (app, other):
            child.kill()
            child.wait()

    installer.install(paths, installer.preflight(paths))
    manifest = installer.load_manifest(paths)
    assert str(browser) not in manifest["binaries"]
    assert not override.exists()
    assert str(override) not in manifest["files"]
    installer.uninstall(paths)
    assert entry.read_text() == original


# Issue 2: a native CLI update stays recognized without trusting unrelated targets.


@pytest.fixture
def versioned_cli(environment, tmp_path):
    paths = environment["paths"]
    versions = tmp_path / "share" / "claude" / "versions"
    first = native(versions / "2.1.300")
    launcher = paths.home / ".local/bin/claude"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(first)
    handler = paths.data / "applications/claude-code-url-handler.desktop"
    handler.parent.mkdir(parents=True, exist_ok=True)
    handler.write_text(
        "[Desktop Entry]\nType=Application\nName=Claude Code URL Handler\nNoDisplay=true\n"
        f'Exec="{launcher}" --handle-uri %u\nMimeType=x-scheme-handler/claude-cli;\n'
    )
    installer.install(paths, installer.preflight(paths))
    return {"launcher": launcher, "first": first, "versions": versions}


def switch(link, target):
    link.unlink()
    link.symlink_to(target)


@pytest.mark.parametrize("legacy", [False, True])
def test_cli_update_is_recognized_by_launcher_and_monitor(environment, versioned_cli, legacy):
    paths = environment["paths"]
    manifest = installer.load_manifest(paths)
    assert str(versioned_cli["launcher"]) in manifest["launchers"]
    if legacy:
        manifest.pop("launchers")
        atomic_write(paths.manifest, json_bytes(manifest))
    old = subprocess.Popen([str(versioned_cli["first"]), "60"])
    try:
        second = native(versioned_cli["versions"] / "2.1.301")
        switch(versioned_cli["launcher"], second)
        assert desktop_launcher.resolve(manifest, str(versioned_cli["launcher"]))
        coverage = monitor.Coverage(paths)
        coverage.refresh()
        assert {str(versioned_cli["first"]), str(second)} <= coverage.binaries
        new = subprocess.Popen([str(second), "60"])
        try:
            selected = {p.pid for p in processes.scan(coverage.binaries)}
            assert {old.pid, new.pid} <= selected
        finally:
            new.kill()
            new.wait()
        report = diagnostics(paths)
        assert report["accepted_updates"] == [str(second)]
        assert not any("now points to" in issue for issue in report["issues"])
    finally:
        old.kill()
        old.wait()


def test_unrelated_cli_replacement_is_rejected(environment, versioned_cli, tmp_path):
    paths = environment["paths"]
    manifest = installer.load_manifest(paths)
    impostor = native(tmp_path / "elsewhere" / "tool")
    switch(versioned_cli["launcher"], impostor)
    with pytest.raises(desktop_launcher.LaunchBlocked, match="changed to"):
        desktop_launcher.resolve(manifest, str(versioned_cli["launcher"]))
    coverage = monitor.Coverage(paths)
    coverage.refresh()
    assert str(impostor) not in coverage.binaries
    issues = diagnostics(paths)["issues"]
    assert any(f"now points to {impostor}" in issue for issue in issues)
    writable = native(versioned_cli["versions"] / "2.1.302")
    writable.chmod(0o777)
    switch(versioned_cli["launcher"], writable)
    assert executables.accepted_update(versioned_cli["launcher"], manifest["binaries"]) is None


# Issue 5: a bad manifest leaves known applications monitored and blocks new launches.


def test_bad_manifest_keeps_last_coverage_and_recovers(environment, caplog):
    paths = environment["paths"]
    installer.install(paths, installer.preflight(paths))
    good = paths.manifest.read_bytes()
    seen, states = [], []

    def between(count):
        states.append(health.current(paths)[0])
        if count == 1:
            atomic_write(paths.manifest, b"{")
        elif count == 2:
            atomic_write(paths.manifest, good)

    Stop, _waits = stop_after(3, between)

    def tick(binaries, previous, *args, **kwargs):
        seen.append(binaries)
        return previous

    caplog.set_level("INFO")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(monitor.threading, "Event", Stop)
        patch.setattr(monitor.signal, "signal", lambda *_: None)
        patch.setattr(monitor, "tick", tick)
        monitor.run(paths)
    assert seen == [{str(environment["desktop"])}] * 3
    assert states == ["ready", "degraded", "ready"]
    assert caplog.text.count("monitor_degraded") == 1
    assert not paths.monitor_health.exists()


def test_cold_start_with_bad_manifest_blocks_desktop_launch(environment, monkeypatch):
    paths = environment["paths"]
    installer.install(paths, installer.preflight(paths))
    atomic_write(paths.manifest, b"[]")
    Stop, _waits = stop_after(1, lambda _count: blocked.append(launch()))
    blocked = []

    def launch():
        with pytest.raises(desktop_launcher.LaunchBlocked, match="not ready") as error:
            desktop_launcher.wait_until_ready(paths, systemd, timeout=0)
        return str(error.value)

    monkeypatch.setattr(monitor.threading, "Event", Stop)
    monkeypatch.setattr(monitor.signal, "signal", lambda *_: None)
    monitor.run(paths)
    assert "unexpected structure" in blocked[0]


def test_stale_readiness_cannot_admit_launch(environment):
    paths = environment["paths"]
    gone = subprocess.Popen(["true"])
    gone.wait()
    atomic_write(paths.monitor_health, json_bytes({"pid": gone.pid, "start": 1, "status": "ready"}))
    assert health.current(paths)[0] == "stopped"
    health.publish(paths, "ready")
    assert health.current(paths) == ("ready", None)
    health.publish(paths, "degraded", "manifest unreadable")
    assert health.current(paths) == ("degraded", "manifest unreadable")


# Issue 6: doctor notices new launch paths that are not guarded.


def test_doctor_reports_new_unguarded_launchers(environment, monkeypatch):
    paths = environment["paths"]
    installer.install(paths, installer.preflight(paths))
    monkeypatch.setenv("PATH", f"{paths.bin}:{os.environ['PATH']}")
    assert diagnostics(paths)["issues"] == []
    second = environment["entry"].with_name("claude-beta.desktop")
    second.write_text(environment["entry"].read_text().replace("Name=Claude", "Name=Claude Beta"))
    autostart = paths.config / "autostart" / "claude-tray.desktop"
    autostart.parent.mkdir(parents=True)
    autostart.write_text(environment["entry"].read_text())
    override = paths.data / "applications" / environment["entry"].name
    override.write_text(
        override.read_text().replace("Actions=New;", "Actions=New;Quick;")
        + f'\n[Desktop Action Quick]\nName=Quick\nExec="{environment["desktop"]}" --quick\n'
    )
    issues = diagnostics(paths)["issues"]
    assert any(f"Unguarded Claude launcher: {second}" in issue for issue in issues)
    assert any(f"Unguarded Claude launcher: {autostart}" in issue for issue in issues)
    assert any("[Desktop Action Quick] launches Claude without the guard" in i for i in issues)
    installer.install(paths, installer.preflight(paths))
    assert diagnostics(paths)["issues"] == []


# Issue 7: one retry for a dropped request, inside the verification deadline.


def flaky(results):
    calls = []

    def request(family):
        calls.append(family)
        outcome = results[family].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return Egress(outcome, "1.1.1.1" if family == 4 else "2606:4700:4700::1111")

    return request, calls


def test_timeout_then_success_is_allowed_without_repeating_successes():
    request, calls = flaky({4: [TimeoutError(), "GB"], 6: ["GB"]})
    verdict = check(routes=lambda: NetworkState((4, 6), "same"), request=request, environ={})
    assert verdict.allowed, verdict.reason
    assert sorted(calls) == [4, 4, 6]


def test_persistent_timeout_and_hard_errors_deny():
    request, calls = flaky({4: [TimeoutError(), TimeoutError()]})
    assert not check(routes=lambda: NetworkState((4,), "same"), request=request, environ={}).allowed
    assert calls == [4, 4]
    request, calls = flaky({4: [ValueError("bad"), "GB"]})
    assert not check(routes=lambda: NetworkState((4,), "same"), request=request, environ={}).allowed
    assert calls == [4]


def test_confirmed_mismatch_is_not_retried():
    request, calls = flaky({4: [TimeoutError(), "GB"], 6: ["US"]})
    verdict = check(routes=lambda: NetworkState((4, 6), "same"), request=request, environ={})
    assert not verdict.allowed
    assert sorted(calls) == [4, 6]


def test_retry_respects_deadline_and_revalidates_routes(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(checker, "DEADLINE_SECONDS", checker.RETRY_SECONDS - 1)
        request, calls = flaky({4: [TimeoutError(), "GB"]})
        assert not check(
            routes=lambda: NetworkState((4,), "same"), request=request, environ={}
        ).allowed
        assert calls == [4]
    states = iter([NetworkState((4,), "a"), NetworkState((4,), "b")])
    request, calls = flaky({4: [TimeoutError(), "GB"]})
    verdict = check(routes=lambda: next(states), request=request, environ={})
    assert not verdict.allowed and "network changed" in verdict.reason


def test_overall_deadline_with_slow_retry(monkeypatch):
    def slow(family):
        time.sleep(0.05)
        raise TimeoutError()

    started = time.monotonic()
    check(routes=lambda: NetworkState((4,), "same"), request=slow, environ={})
    assert time.monotonic() - started < checker.DEADLINE_SECONDS


@pytest.mark.parametrize("code,transient", [(28, True), (7, True), (60, False), (22, False)])
def test_curl_failures_are_classified(monkeypatch, code, transient):
    monkeypatch.setattr(
        subprocess, "run", Mock(side_effect=subprocess.CalledProcessError(code, ["curl"]))
    )
    with pytest.raises(ProviderFailure) as error:
        query(4, "claude.ai")
    assert error.value.transient is transient
