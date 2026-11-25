from unittest.mock import Mock

import pytest

from claude_geo_guard import cli


def test_no_command_prints_guide_without_probing(monkeypatch, capsys):
    probe = Mock(side_effect=AssertionError("help must not probe the system"))
    monkeypatch.setattr(cli, "backend", probe)
    assert cli.main([]) == 0
    output = capsys.readouterr()
    assert "claude-geo-guard logs -f" in output.out
    assert "bootstrap.py" not in output.out
    assert not output.err


def test_logs_filters_and_exit_status(monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "linux")
    run = Mock(return_value=Mock(returncode=7))
    monkeypatch.setattr(cli.subprocess, "run", run)
    assert cli.main(["logs", "-f", "-n", "0", "--since", "10 minutes ago", "--json"]) == 7
    assert run.call_args.args[0] == [
        "journalctl",
        "--user",
        "--unit=claude-geo-guard.service",
        "--lines=0",
        "--output=json",
        "--follow",
        "--since",
        "10 minutes ago",
        "--no-pager",
    ]


def test_logs_interrupt_is_success(monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setattr(cli.subprocess, "run", Mock(side_effect=KeyboardInterrupt))
    assert cli.main(["logs", "-f"]) == 0


@pytest.mark.parametrize("value", ["-1", "oops"])
def test_invalid_log_count_is_rejected(value, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["logs", "-n", value])
    assert error.value.code == 2
    assert "claude-geo-guard logs" in capsys.readouterr().err


def test_logs_missing_journalctl_is_readable(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setattr(cli.subprocess, "run", Mock(side_effect=FileNotFoundError("journalctl")))
    assert cli.main(["logs"]) == 2
    assert "journalctl" in capsys.readouterr().err
