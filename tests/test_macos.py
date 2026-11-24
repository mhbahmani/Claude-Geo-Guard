import sys
from subprocess import CompletedProcess
from unittest.mock import Mock

import pytest

from claude_geo_guard import installer
from claude_geo_guard.paths import Paths
from claude_geo_guard.platforms.macos import environment, networking


def test_cli_install_roundtrip(isolated, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(environment.shutil, "which", lambda name: name)
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\necho '2.1.295 (Claude Code)'\n")
    binary.chmod(0o755)
    paths = Paths.current()
    with pytest.raises(ValueError, match="skip-desktop"):
        installer.preflight(paths, skip_desktop=True, desktop_app="/unused")
    assert not paths.guard_config.exists()
    for _ in range(2):
        plan = installer.preflight(paths, skip_desktop=True, claude_binary=str(binary))
        installer.install(paths, plan)
    assert "Application Support" in str(paths.manifest)
    assert paths.settings.exists()
    installer.uninstall(paths)
    assert not paths.settings.exists() and not paths.manifest.exists()


def test_ipv4_only(monkeypatch):
    responses = [
        (0, "<dictionary> {\n}\n", ""),
        (0, "interface: utun0", ""),
        (0, "flags=UP\n inet 10.0.0.1", ""),
        (1, "", "not in table"),
    ]
    monkeypatch.setattr(
        networking, "run", Mock(side_effect=[CompletedProcess([], *r) for r in responses])
    )
    assert networking.snapshot().families == (4,)


@pytest.mark.parametrize("output", ["", "<dictionary> {\n ProxyAutoConfigEnable : 1\n}"])
def test_ambiguous_proxy_denies(monkeypatch, output):
    monkeypatch.setattr(networking, "run", Mock(return_value=CompletedProcess([], 0, output, "")))
    with pytest.raises(ValueError, match="proxy"):
        networking.snapshot()


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS routing tools")
def test_native_cli_only_prerequisites():
    environment.preflight(skip_desktop=True, desktop_app=None)


@pytest.mark.parametrize("case", ["allowed", "denied", "inactive", "changed", "stopped"])
def test_guarded_launch(isolated, monkeypatch, case):
    from claude_geo_guard.platforms.macos import application
    from claude_geo_guard.checker import Verdict
    from claude_geo_guard.storage import atomic_write, json_bytes

    from claude_geo_guard.config import DEFAULTS

    atomic_write(isolated.guard_config, json_bytes(DEFAULTS))
    record = {"path": str(isolated.home / "Claude Test.app")}
    atomic_write(isolated.manifest, json_bytes({"mac_app": record}))
    monkeypatch.setattr(
        application.service, "healthy", Mock(side_effect=[case != "inactive", case != "stopped"])
    )
    monkeypatch.setattr(
        application,
        "check",
        lambda: Verdict(case != "denied", "policy", {4: "GB"}, "GB", {4: "1.1.1.1"}),
    )
    monkeypatch.setattr(application, "identify", lambda _: {} if case == "changed" else record)
    monkeypatch.setattr(application, "launch_environment", lambda _: {"TZ": "Europe/London"})
    launch = Mock()
    monkeypatch.setattr(application.subprocess, "run", launch)
    monkeypatch.setattr(application.dialogs, "notify", Mock())
    if case == "allowed":
        application.launch(isolated, [])
        assert launch.call_args.args[0] == [
            "/usr/bin/open",
            "--env",
            "TZ=Europe/London",
            "-a",
            record["path"],
        ]
    else:
        with pytest.raises(ValueError):
            application.launch(isolated, [])
        launch.assert_not_called()


@pytest.mark.skipif(sys.platform != "darwin", reason="requires native macOS applet compiler")
def test_native_bundle_and_monitor(isolated, tmp_path):
    import subprocess
    from claude_geo_guard.platforms.macos import application, processes, service
    from claude_geo_guard.storage import atomic_write
    import plistlib

    import shlex

    marker = tmp_path / "guard-invoked"
    atomic_write(
        isolated.bin / "claude-geo-guard",
        ("#!/bin/sh\nprintf '%s' \"$1\" > " + shlex.quote(str(marker)) + "\n").encode(),
        0o755,
    )
    application.build(isolated, atomic_write)
    app = application.destination(isolated)
    subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)], check=True)
    subprocess.run(
        ["/usr/bin/osascript", str(app / "Contents/Resources/Scripts/main.scpt")],
        check=True,
        timeout=15,
    )
    assert marker.read_text() == "desktop"
    assert plistlib.loads(service.contents(isolated))["ProgramArguments"] == [
        str(isolated.bin / "claude-geo-guard"),
        "monitor",
    ]
    exe = tmp_path / "synthetic-app"
    subprocess.run(
        ["/usr/bin/cc", "-x", "c", "-", "-o", str(exe)],
        input="#include <unistd.h>\nint main(void) { sleep(20); return 0; }\n",
        text=True,
        check=True,
        timeout=60,
    )
    with subprocess.Popen([exe, "20"]) as child:
        try:
            found = processes.scan({str(exe.resolve())})
            assert [p.pid for p in found] == [child.pid]
            processes.terminate(found, grace=0.1)
            assert child.wait(timeout=3) < 0
        finally:
            if child.poll() is None:
                child.kill()


def test_disposable_runner_launchagent(isolated, tmp_path, monkeypatch):
    """A temporary HOME alone cannot isolate launchd; opt in on disposable CI only."""
    import os
    import uuid
    from claude_geo_guard.platforms.macos import application, service

    if (
        sys.platform != "darwin"
        or os.environ.get("GUARD_CI_LAUNCHD") != "1"
        or os.environ.get("GITHUB_ACTIONS") != "true"
    ):
        pytest.skip("requires disposable macOS runner with a GUI domain")
    monkeypatch.setattr(service, "LABEL", "local.guard-ci." + uuid.uuid4().hex)
    fake = tmp_path / "unused-claude"
    fake.write_text("#!/bin/sh\necho '2.1.295 (Claude Code)'\n")
    fake.chmod(0o755)
    record = {"path": str(tmp_path / "Example.app"), "executable": str(tmp_path / "nonrunning-app")}
    monkeypatch.setattr(application, "identify", lambda path: record)
    plan = installer.preflight(isolated, claude_binary=str(fake), desktop_app=record["path"])
    try:
        installer.install(isolated, plan)
        assert service.healthy(isolated)
        assert application.destination(isolated).is_dir()
        installer.uninstall(isolated)
        assert not service.status(isolated)
        assert not application.destination(isolated).exists()
    finally:
        service.stop(isolated)
