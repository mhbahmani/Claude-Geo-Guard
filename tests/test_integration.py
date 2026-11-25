import json
import os
import sys
import signal
import subprocess
import time
from pathlib import Path

from claude_geo_guard import installer

ROOT = Path(__file__).resolve().parents[1]


def test_full_install_cli_hooks_desktop_uninstall(environment, monkeypatch):
    paths = environment["paths"]
    # Exercise actual entrypoint, not only Python installer functions.
    installed = subprocess.run(
        [sys.executable, "-I", str(ROOT / "run.py"), "install", "--yes"],
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, installed.stderr
    cli = paths.bin / "claude"
    guard = paths.bin / "claude-geo-guard"
    monkeypatch.setenv("PATH", f"{paths.bin}:{os.environ['PATH']}")
    result = subprocess.run(
        [str(cli), "hello world", "--flag", "$(no-shell)"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "Claude Geo Guard: launch checks passed" in result.stderr
    assert json.loads(result.stdout) == ["hello world", "--flag", "$(no-shell)"]
    first_option = subprocess.run(
        [str(cli), "--help", "-p", "prompt"], capture_output=True, text=True
    )
    assert first_option.returncode == 0, first_option.stderr
    assert json.loads(first_option.stdout) == ["--help", "-p", "prompt"]
    version = subprocess.run([str(cli), "--version"], capture_output=True, text=True)
    assert version.returncode == 0 and "2.1.295" in version.stdout
    monkeypatch.setenv("FAKE_EXIT", "17")
    assert subprocess.run([str(cli)], capture_output=True).returncode == 17
    (environment["bins"] / "FAKE_COUNTRY").write_text("US")
    denied_cli = subprocess.run([str(cli)], capture_output=True, text=True)
    assert denied_cli.returncode == 2
    assert "Claude Geo Guard blocked CLI launch" in denied_cli.stderr
    assert "launch checks passed" not in denied_cli.stderr
    assert subprocess.run([str(guard), "hook"], capture_output=True).returncode == 2
    # Exercise the installed desktop denial path and its window, without a real display.
    helper = environment["bins"] / "zenity"
    helper.write_text(
        "#!/usr/bin/python3\nimport json, os, sys\nfrom pathlib import Path\n"
        '(Path(os.environ["HOME"]) / "dialog.json").write_text(json.dumps(sys.argv[1:]))\n'
    )
    monkeypatch.setenv("DISPLAY", ":fake-test-display")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    denied = subprocess.run(
        [str(guard), "desktop", "--", str(environment["desktop"]), "0"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert denied.returncode == 2, denied.stderr
    dialog_args = json.loads((paths.home / "dialog.json").read_text())
    assert "--error" in dialog_args and "--extra-button=Show details" in dialog_args
    assert "--ok-label=Close" in dialog_args
    assert "Allowed country: <b>GB</b>" in dialog_args[-1]
    assert "Detected IPv4: <b>US · 1.1.1.1</b>" in dialog_args[-1]
    subprocess.run([str(guard), "disable"], check=True)
    unguarded = subprocess.run([str(cli), "hello"], capture_output=True, text=True)
    assert unguarded.returncode == 17
    assert "claude-geo-guard enable" in unguarded.stderr
    assert json.loads(unguarded.stdout) == ["hello"]
    subprocess.run([str(guard), "hook"], check=True)
    subprocess.run([str(guard), "desktop", "--", str(environment["desktop"]), "0"], check=True)
    warning = json.loads((paths.home / "dialog.json").read_text())
    assert "--warning" in warning and "claude-geo-guard enable" in warning[-1]
    subprocess.run([str(guard), "enable"], check=True)
    assert subprocess.run([str(guard), "hook"], capture_output=True).returncode == 2
    (environment["bins"] / "FAKE_COUNTRY").write_text("GB")
    assert subprocess.run([str(guard), "hook"], capture_output=True).returncode == 0
    report = subprocess.run([str(guard), "doctor"], capture_output=True, text=True)
    assert report.returncode == 0, report.stdout + report.stderr
    assert json.loads(report.stdout)["hooks_registered"]
    helper.write_text(
        "#!/usr/bin/python3\nimport json, os, sys\nfrom pathlib import Path\n"
        'if "--info" not in sys.argv: raise SystemExit(0)\n'
        '(Path(os.environ["HOME"]) / "success-message.json").write_text(json.dumps(sys.argv[1:]))\n'
    )
    # Real native fake desktop launch via guard; wrapper exec preserves signal status.
    app = subprocess.Popen([str(guard), "desktop", "--", str(environment["desktop"]), "60"])
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                if (
                    os.readlink(f"/proc/{app.pid}/exe") == str(environment["desktop"])
                    and (paths.home / "success-message.json").exists()
                ):
                    break
            except FileNotFoundError:
                pass
            time.sleep(0.02)
        else:
            raise AssertionError("guard did not exec native desktop")
        confirmation = json.loads((paths.home / "success-message.json").read_text())
        assert "Country matches your setting: GB" in confirmation[-1]
        assert "GB · 1.1.1.1" in confirmation[-1]
        app.terminate()
        assert app.wait(timeout=3) == -signal.SIGTERM
    finally:
        if app.poll() is None:
            app.kill()
            app.wait()
    uninstalled = subprocess.run([str(ROOT / "uninstall.sh")], capture_output=True, text=True)
    assert uninstalled.returncode == 0, uninstalled.stderr
    assert not cli.exists() and not paths.settings.exists()
    assert not paths.manifest.exists()
    assert environment["entry"].exists()


def test_live_monitor_kills_only_protected_native_process(environment, monkeypatch):
    paths = environment["paths"]
    installer.install(paths, installer.preflight(paths))
    (environment["bins"] / "FAKE_CURL_EXIT").write_text("28")
    monkeypatch.setenv("DISPLAY", ":fake-monitor-display")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    (environment["bins"] / "zenity").write_text(
        "#!/usr/bin/python3\nimport json, os, sys\nfrom pathlib import Path\n"
        '(Path(os.environ["HOME"]) / "runtime-dialog.json").write_text(json.dumps(sys.argv[1:]))\n'
    )
    app = subprocess.Popen([str(environment["desktop"]), "60"])
    other = subprocess.Popen(["/bin/sleep", "60"])
    mon = subprocess.Popen(
        [str(paths.bin / "claude-geo-guard"), "monitor", "--interval", "5", "--grace", "0.1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert app.wait(timeout=10) == -signal.SIGTERM
        assert other.poll() is None
        dialog = paths.home / "runtime-dialog.json"
        deadline = time.monotonic() + 5
        while not dialog.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert dialog.exists(), "monitor did not invoke runtime dialog"
        dialog_args = json.loads(dialog.read_text())
        assert "Claude was closed by the guard" in dialog_args[-1]
        assert "was closed" in dialog_args[-1]
        assert mon.poll() is None
        mon.terminate()
        mon.wait(timeout=5)
        assert mon.returncode == 0
        logs = mon.stderr.read()
        assert b'"allowed": false' in logs
        assert b"provider request timed out (curl exit 28" in logs
        assert b"runtime_stop_requested" in logs and b"runtime_stop_finished" in logs
    finally:
        for child in (app, other, mon):
            if child.poll() is None:
                child.kill()
            child.wait()
        installer.uninstall(paths)


def test_pending_launch_waits_for_verification(environment, monkeypatch):
    from conftest import script

    paths = environment["paths"]
    bins = environment["bins"]
    installer.install(paths, installer.preflight(paths))
    # Hold every provider request until the test releases it, then simulate the screenshot timeout.
    script(
        bins / "curl",
        """from pathlib import Path
import time
here = Path(__file__).parent
(here / 'checking').touch()
while not (here / 'release').exists():
    time.sleep(.02)
raise SystemExit(28)
""",
    )
    script(
        bins / "zenity",
        """from pathlib import Path
import os, sys, time
here = Path(__file__).parent
if '--progress' in sys.argv:
    (here / 'progress').write_text(str(os.getpid()))
    time.sleep(20)
elif '--error' in sys.argv:
    (here / 'failure').touch()
""",
    )
    monkeypatch.setenv("DISPLAY", ":fake")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    cli = paths.bin / "claude"
    desktop = environment["desktop"]
    guard = paths.bin / "claude-geo-guard"
    # If either wrapper starts Claude prematurely, that process will replace it.
    with subprocess.Popen([cli, "pending"], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as code:
        with subprocess.Popen([guard, "desktop", "--", desktop, "60"]) as app:
            try:
                deadline = time.monotonic() + 5
                while not all((bins / name).exists() for name in ("checking", "progress")):
                    assert time.monotonic() < deadline
                    time.sleep(0.02)
                assert code.poll() is None and app.poll() is None
                assert Path(f"/proc/{app.pid}/exe").resolve() != desktop
            finally:
                (bins / "release").touch()
            assert app.wait(timeout=5) == 2
            assert (bins / "failure").exists()
            assert not Path("/proc", (bins / "progress").read_text()).exists()
        stdout, stderr = code.communicate(timeout=5)
        assert code.returncode == 2 and not stdout
        assert b"Checking your public IP and country" in stderr
