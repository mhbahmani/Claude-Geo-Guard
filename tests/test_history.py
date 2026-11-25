import json
from unittest.mock import Mock

import pytest

from claude_geo_guard import cli, history


def record(reason="provider timed out", **values):
    event = {"event": "runtime_stop_requested", "reason": reason, **values}
    return json.dumps({"__REALTIME_TIMESTAMP": "1700000000000000", "MESSAGE": json.dumps(event)})


def journal(monkeypatch, output="", code=0, error=""):
    monkeypatch.setattr(history.sys, "platform", "linux")
    run = Mock(return_value=Mock(stdout=output, stderr=error, returncode=code))
    monkeypatch.setattr(history.subprocess, "run", run)
    return run


def test_latest_valid_closure(monkeypatch):
    run = journal(
        monkeypatch, "\n".join(["invalid", record("new", countries={"4": "DE"}), record("old")])
    )
    event = history.last_stop()
    assert event["reason"] == "new"
    assert "IPv4: DE" in history.report(event)
    assert "--reverse" in run.call_args.args[0]
    assert "--grep=runtime_stop_requested" in run.call_args.args[0]


def test_no_retained_record(monkeypatch):
    journal(monkeypatch, code=1)
    assert history.last_stop() is None
    assert "Older entries may have expired" in history.report(None)


def test_journal_failure_is_not_no_history(monkeypatch):
    journal(monkeypatch, code=1, error="permission denied")
    with pytest.raises(ValueError, match="could not read"):
        history.last_stop()


def test_bad_records_are_reported(monkeypatch):
    journal(monkeypatch, '[]\n{"MESSAGE": []}')
    with pytest.raises(ValueError, match="could not be parsed"):
        history.last_stop()


def test_report_handles_legacy_missing_fields_and_control_characters(monkeypatch):
    journal(monkeypatch, record("timeout\x1b[31m\n"))
    result = history.report(history.last_stop())
    assert "Not verified" in result
    assert "termination request" in result
    assert "\x1b" not in result


def test_command_uses_history_without_live_check(monkeypatch, capsys):
    monkeypatch.setattr(history, "last_stop", lambda: None)
    monkeypatch.setattr(cli, "check", Mock(side_effect=AssertionError("no live check")))
    assert cli.main(["last-stop"]) == 0
    assert "No recorded monitor closure" in capsys.readouterr().out


def test_status_removed_and_installer_helpers(capsys):
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["status"])
    cli.install_help()
    output = capsys.readouterr().out
    assert output.count("claude-geo-guard") == 3
    assert "last-stop" in output
