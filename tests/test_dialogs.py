import subprocess
import sys
import threading
from unittest.mock import MagicMock, Mock

import pytest

from claude_geo_guard import cli, desktop_launcher
from claude_geo_guard.checker import Verdict
from claude_geo_guard.platforms.linux import dialogs


@pytest.mark.parametrize("desktop,helper", [("ubuntu:GNOME", "zenity"), ("KDE", "kdialog")])
def test_dialog_uses_desktop_helper_and_safe_text(monkeypatch, desktop, helper):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
    monkeypatch.setattr(dialogs.shutil, "which", lambda name: f"/usr/bin/{name}")
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    if helper == "zenity":
        run.side_effect = [
            subprocess.CompletedProcess([], 1, stdout=b"Show details\n"),
            subprocess.CompletedProcess([], 0),
        ]
    monkeypatch.setattr(dialogs.subprocess, "run", run)
    assert dialogs.show_error('GB required; PL detected\n<unsafe> $(command) "quote"')
    args = run.call_args.args[0]
    assert args[0] == helper
    assert "PL detected" in args[-1]
    assert "$(command)" in args[-1]
    assert not run.call_args.kwargs.get("shell")
    if helper == "zenity":
        assert "--no-markup" in args
        assert "--ok-label=Close" in args
        assert "--extra-button=Show details" in run.call_args_list[0].args[0]
    else:
        assert "&lt;unsafe&gt;" in args[-1]
        assert "--detailederror" in args
        assert args[args.index("--ok-label") + 1] == "Close"


def test_missing_preferred_helper_uses_other(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setattr(dialogs.shutil, "which", lambda name: name if name == "zenity" else None)
    assert dialogs.available() == "zenity"


@pytest.mark.parametrize(
    "failure", ["missing", "headless", "exec_error", "bad_exit", "display_error"]
)
def test_unavailable_window_falls_back_without_allowing(monkeypatch, failure):
    if failure != "headless":
        monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(dialogs, "available", lambda: None if failure == "missing" else "zenity")
    run = Mock(return_value=subprocess.CompletedProcess([], 2))
    if failure == "exec_error":
        run.side_effect = OSError("no helper")
    if failure == "display_error":
        run.return_value = subprocess.CompletedProcess([], 1, stderr=b"cannot open display")
    monkeypatch.setattr(dialogs.subprocess, "run", run)
    assert dialogs.show_error("denied") is False
    assert run.call_args.args[0][0] == "notify-send"


def test_dismiss_window_is_not_a_retry(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(dialogs, "available", lambda: "zenity")
    run = Mock(return_value=subprocess.CompletedProcess([], 1))
    monkeypatch.setattr(dialogs.subprocess, "run", run)
    assert dialogs.show_error("denied")
    assert run.call_count == 1


@pytest.mark.parametrize("error", [ValueError("monitor inactive"), OSError("missing executable")])
def test_desktop_errors_show_window_and_exit_denied(monkeypatch, capsys, error):
    monkeypatch.setattr(desktop_launcher, "launch", Mock(side_effect=error))
    window = Mock(return_value=True)
    monkeypatch.setattr(dialogs, "show_error", window)
    assert cli.main(["desktop", "--", "/fake/desktop"]) == 2
    window.assert_called_once_with(error)
    assert str(error) in capsys.readouterr().err


@pytest.mark.parametrize("command", ["check", "hook", "code"])
def test_non_desktop_commands_never_open_window(monkeypatch, command):
    monkeypatch.setattr(cli, "check", lambda: Verdict(False, "test denial", {}))
    monkeypatch.setattr(cli.cli_launcher, "launch", Mock(side_effect=ValueError("denied")))
    window = Mock()
    monkeypatch.setattr(dialogs, "show_error", window)
    assert cli.main([command]) == 2
    window.assert_not_called()


def test_no_window_remains_denied_with_remediation(monkeypatch, capsys):
    monkeypatch.setattr(desktop_launcher, "launch", Mock(side_effect=ValueError("denied")))
    monkeypatch.setattr(dialogs, "show_error", lambda reason: False)
    assert cli.main(["desktop"]) == 2
    assert "Install zenity" in capsys.readouterr().err


def test_country_summary_has_hierarchy_and_recovery():
    verdict = Verdict(False, "egress outside configured country GB", {4: "PL", 6: "GB"}, "GB")
    error = desktop_launcher.LaunchBlocked(verdict.reason, kind="verification", verdict=verdict)
    message = dialogs.describe(error)
    assert message.heading == "Connection outside allowed country"
    assert message.facts == (
        ("Allowed country", "GB"),
        ("Detected IPv4", "PL · IP not returned"),
        ("Detected IPv6", "GB · IP not returned"),
        ("IP filter", "Off"),
        ("Country provider", "Claude edge traces (plus ifconfig.io in full mode)"),
    )
    assert "Applications" in message.next_step
    assert "claude-geo-guard doctor" not in message.plain()
    assert verdict.reason in message.details
    assert "claude-geo-guard doctor" in message.details
    assert '<span size="large" weight="bold">' in message.markup("zenity")
    assert "IPv4: <b>PL · IP not returned</b>" in message.markup("zenity")
    assert "foreground=" not in message.markup("zenity")  # Native theme owns contrast.


@pytest.mark.parametrize(
    "reason,heading,action",
    [
        ("verification failed (TimeoutExpired)", "Couldn’t verify", "Check your connection"),
        ("no usable public routes", "Couldn’t verify", "Check your connection"),
        ("network changed during verification", "Couldn’t verify", "settle"),
        ("proxy environment is ambiguous; use a routed VPN", "Couldn’t verify", "proxy settings"),
        (
            "country policy is invalid or unreadable; repair or reinstall",
            "Check your country",
            "configuration",
        ),
    ],
)
def test_unverified_is_never_presented_as_country_mismatch(reason, heading, action):
    error = desktop_launcher.LaunchBlocked(
        reason, kind="verification", verdict=Verdict(False, reason, {}, "GB")
    )
    message = dialogs.describe(error)
    assert message.heading.startswith(heading)
    assert action in message.next_step
    assert ("Detected country / IP", "Not verified · IP not returned") in message.facts
    assert "outside allowed country" not in message.heading


@pytest.mark.parametrize("kind,action", [("monitor", "Restart"), ("executable", "installer")])
def test_non_location_failure_has_specific_recovery(kind, action):
    message = dialogs.describe(desktop_launcher.LaunchBlocked("technical reason", kind=kind))
    assert action in message.next_step
    assert not message.facts
    assert "technical reason" not in message.plain()
    assert "technical reason" in message.details


@pytest.mark.parametrize("helper", ["zenity", "kdialog"])
def test_all_dynamic_markup_is_escaped(helper):
    unsafe = '<a href="https://example.com">click</a> & \\074b\\076'
    message = dialogs.Message(unsafe, unsafe, unsafe, ((unsafe, unsafe),), unsafe)
    markup = message.markup(helper)
    assert "<a href=" not in markup
    assert "&lt;a href=&quot;" in markup
    if helper == "zenity":
        assert "\\\\074b\\\\076" in markup  # Cannot decode escapes into markup.


def test_kde_window_close_is_dismissal(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":fake")
    monkeypatch.setattr(dialogs, "available", lambda: "kdialog")
    run = Mock(return_value=subprocess.CompletedProcess([], 2))
    monkeypatch.setattr(dialogs.subprocess, "run", run)
    assert dialogs.show_error(ValueError("denied"))
    assert run.call_count == 1


def test_details_failure_notifies_and_still_denies(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":fake")
    monkeypatch.setattr(dialogs, "available", lambda: "zenity")
    run = Mock(
        side_effect=[
            subprocess.CompletedProcess([], 1, stdout=b"Show details\n"),
            OSError("helper disappeared"),
            subprocess.CompletedProcess([], 0),
        ]
    )
    monkeypatch.setattr(dialogs.subprocess, "run", run)
    assert not dialogs.show_error(ValueError("denied"))
    assert run.call_args.args[0][0] == "notify-send"


def test_runtime_message_explains_closure_and_points_to_log():
    verdict = Verdict(
        False, "verification failed: IPv4: provider request timed out (curl exit 28)", {}, "GB"
    )
    error = desktop_launcher.LaunchBlocked(verdict.reason, kind="verification", verdict=verdict)
    message = dialogs.describe(error, runtime=True)
    assert message.heading == "Claude was closed by the guard"
    assert "was closed" in message.explanation
    assert "stayed closed" not in message.plain()
    assert "curl exit 28" in message.details
    assert "journalctl --user" in message.details
    assert ("Detected country / IP", "Not verified · IP not returned") in message.facts


def test_shutdown_skips_runtime_window_and_notification(monkeypatch):
    stop = threading.Event()
    stop.set()
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    assert not dialogs.show_error(ValueError("offline"), runtime=True, stop=stop)
    run.assert_not_called()


def test_runtime_dialog_child_exits_when_monitor_stops():
    stop = threading.Event()
    stop.set()
    result = dialogs._run_dialog([sys.executable, "-c", "import time; time.sleep(60)"], stop)
    assert result.returncode < 0


def test_runtime_dialog_force_stops_unresponsive_helper(monkeypatch):
    stop = threading.Event()
    stop.set()
    child = MagicMock()
    child.__enter__.return_value = child
    child.returncode = -9
    child.communicate.side_effect = [
        subprocess.TimeoutExpired("helper", 0.2),
        subprocess.TimeoutExpired("helper", 1),
        (b"", b""),
    ]
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=child))
    result = dialogs._run_dialog(["helper"], stop)
    assert result.returncode == -9
    child.terminate.assert_called_once()
    child.kill.assert_called_once()


@pytest.mark.parametrize("helper", ["zenity", "kdialog"])
def test_success_popup_has_timeout_and_ok_button(monkeypatch, helper):
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(dialogs, "available", lambda: helper)
    spawn = Mock()
    monkeypatch.setattr(subprocess, "Popen", spawn)
    dialogs.show_success(Verdict(True, "verified", {4: "GB"}, "GB"))
    args = spawn.call_args.args[0]
    if helper == "zenity":
        assert "--timeout=5" in args and "--ok-label=OK" in args
    else:
        assert args[:4] == ["timeout", "--foreground", "5s", "kdialog"]
        assert "--msgbox" in args
    spawn.return_value.wait.assert_not_called()
