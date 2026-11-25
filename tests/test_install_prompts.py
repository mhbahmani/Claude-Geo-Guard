import sys

from claude_geo_guard import cli, config, installer
from claude_geo_guard.storage import atomic_write, json_bytes


def terminal(monkeypatch, answers=None):
    prompts = []

    def respond(prompt):
        prompts.append(prompt)
        return (answers or {}).get(prompt.split(" [")[0], "")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", respond)
    return prompts


def test_no_flag_install_enter_then_reinstall(environment, monkeypatch):
    paths = environment["paths"]
    prompts = terminal(monkeypatch)
    assert cli.main(["install"]) == 0
    assert config.load(paths) == config.DEFAULTS
    assert any("Allowed country" in p and "[GB]" in p for p in prompts)
    assert "Also check your public IP using ifconfig.io? [yes]: " in prompts
    assert prompts[-1] == "Apply these changes? [yes]: "
    saved = dict(
        config.DEFAULTS,
        allowed_country="DE",
        interval=22,
        grace=0,
        timezone="Europe/Berlin",
        allowed_ips=["1.1.1.1"],
        split_tunnel=True,
        success_notifications=False,
    )
    atomic_write(paths.guard_config, json_bytes(saved))
    prompts.clear()
    assert cli.main(["install"]) == 0
    assert config.load(paths) == saved
    assert any("Allowed country" in p and "[DE]" in p for p in prompts)
    assert "Also check your public IP using ifconfig.io? [no]: " in prompts
    paths.guard_config.unlink()
    assert cli.main(["install"]) == 0
    assert config.load(paths) == config.DEFAULTS


def test_explicit_flags_and_cancel(environment, monkeypatch):
    prompts = terminal(monkeypatch, {"Apply these changes?": "no"})
    assert cli.main(["install", "--country", "FR", "--skip-desktop", "--no-split-tunnel"]) == 2
    assert not any("Allowed country" in p or "ifconfig.io" in p or "Desktop" in p for p in prompts)
    assert not environment["paths"].manifest.exists()
    prompts.clear()
    assert cli.main(["install", "--dry-run"]) == 0
    assert cli.main(["install", "--yes", "--skip-desktop"]) == 0
    assert not prompts


def test_invalid_answer_retries_one_field(environment, monkeypatch):
    answers = iter(["UK", "de"])

    def respond(prompt):
        if prompt.startswith("Allowed country"):
            return next(answers)
        return ""

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", respond)
    assert cli.main(["install", "--skip-desktop"]) == 0
    assert config.load(environment["paths"])["allowed_country"] == "DE"
    assert not installer.load_manifest(environment["paths"])["desktops"]


def test_install_disabled(environment, monkeypatch, capsys):
    terminal(monkeypatch, {"Enable Claude Geo Guard now?": "no"})
    assert cli.main(["install"]) == 0
    assert not config.load(environment["paths"])["enabled"]
    assert "claude-geo-guard enable" in capsys.readouterr().out
