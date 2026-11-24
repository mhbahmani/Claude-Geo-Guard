import json
import os
import subprocess
from unittest.mock import Mock

import pytest

from claude_geo_guard import checker, config, installer, monitor
from claude_geo_guard.cli import main
from claude_geo_guard.platforms.linux.networking import NetworkState
from claude_geo_guard.policy import load_country, normalize_country
from claude_geo_guard.platforms.linux.processes import Process
from claude_geo_guard.storage import atomic_write, json_bytes


@pytest.mark.parametrize("value,expected", [("GB", "GB"), (" de ", "DE"), ("us", "US")])
def test_country_normalization(value, expected):
    assert normalize_country(value) == expected


@pytest.mark.parametrize("value", ["UK", "ZZ", "", "Germany", "GB,DE", None, 12, ["GB"]])
def test_invalid_country(value):
    with pytest.raises(ValueError, match="alpha-2"):
        normalize_country(value)


def test_default_policy_before_installation(isolated):
    assert load_country(isolated) == "GB"


@pytest.mark.parametrize(
    "raw", [b"{", b"[]", b'{"schema":1}', b'{"schema":2}', b'{"schema":2,"allowed_country":"ZZ"}']
)
def test_invalid_saved_policy_fails_closed(isolated, raw):
    atomic_write(isolated.guard_config, raw)
    request = Mock()
    result = checker.check(request=request, environ={})
    assert not result.allowed and "configuration" in result.reason
    request.assert_not_called()


def test_missing_installed_policy_fails_closed(isolated):
    isolated.bin.mkdir(parents=True)
    (isolated.bin / "claude-geo-guard").touch()
    assert not checker.check(environ={}).allowed


@pytest.mark.parametrize(
    "countries,allowed",
    [
        ({4: "DE"}, True),
        ({6: "DE"}, True),
        ({4: "DE", 6: "DE"}, True),
        ({4: "DE", 6: "GB"}, False),
        ({4: "GB"}, False),
    ],
)
def test_custom_country_shared_policy(isolated, countries, allowed):
    atomic_write(isolated.guard_config, json_bytes(dict(config.DEFAULTS, allowed_country="DE")))
    verdict = checker.check(
        routes=lambda: NetworkState(tuple(countries), "same"),
        request=lambda family: checker.Egress(
            countries[family], "1.1.1.1" if family == 4 else "2606:4700:4700::1111"
        ),
        environ={},
    )
    assert verdict.allowed == allowed
    assert verdict.allowed_country == "DE"
    assert "DE" in verdict.reason


def test_policy_change_during_verification_denies(isolated):
    atomic_write(isolated.guard_config, json_bytes(dict(config.DEFAULTS, allowed_country="GB")))

    def request(_):
        atomic_write(isolated.guard_config, json_bytes(dict(config.DEFAULTS, allowed_country="DE")))
        return checker.Egress("GB", "1.1.1.1")

    verdict = checker.check(routes=lambda: NetworkState((4,), "same"), request=request, environ={})
    assert not verdict.allowed and "policy changed" in verdict.reason


def test_custom_install_change_reinstall_and_uninstall(environment):
    paths = environment["paths"]
    plan = installer.preflight(paths, country="de")
    assert installer.summary(plan)["allowed_country"] == "DE"
    assert not paths.manifest.exists()  # Planning remains read-only.
    installer.install(paths, plan)
    before_settings = paths.settings.read_bytes()
    before = installer.load_manifest(paths)
    result = installer.set_country(paths, "fr")
    assert result["allowed_country"] == "FR"
    after = installer.load_manifest(paths)
    assert {k: v for k, v in before.items() if k != "allowed_country"} == {
        k: v for k, v in after.items() if k != "allowed_country"
    }
    assert paths.settings.read_bytes() == before_settings
    installer.install(paths, installer.preflight(paths))
    assert load_country(paths) == "FR"
    installer.install(paths, installer.preflight(paths, country="GB"))
    assert load_country(paths) == "GB"
    installer.uninstall(paths)
    assert not paths.settings.exists() and not paths.manifest.exists()


def test_invalid_changes_leave_policy_and_hooks_intact(environment, monkeypatch):
    paths = environment["paths"]
    installer.install(paths, installer.preflight(paths, country="DE"))
    before = paths.guard_config.read_bytes()
    hooks = paths.settings.read_bytes()
    assert main(["set-country", "UK"]) == 2
    with pytest.raises(ValueError):
        installer.preflight(paths, country="unknown")
    assert paths.guard_config.read_bytes() == before
    monkeypatch.setattr("os.replace", Mock(side_effect=OSError("disk failure")))
    with pytest.raises(OSError):
        installer.set_country(paths, "FR")
    assert paths.guard_config.read_bytes() == before
    assert paths.settings.read_bytes() == hooks


def test_set_country_requires_installation(isolated):
    with pytest.raises(ValueError, match="install the guard first"):
        installer.set_country(isolated, "DE")


def test_cli_hooks_desktop_and_monitor_use_updated_policy(environment, monkeypatch):
    paths = environment["paths"]
    # Exercise the public installation and configuration commands.
    assert main(["install", "--yes", "--country", "de"]) == 0
    guard = str(paths.bin / "claude-geo-guard")
    cli = str(paths.bin / "claude")
    monkeypatch.setenv("PATH", f"{paths.bin}:{os.environ['PATH']}")
    (environment["bins"] / "FAKE_COUNTRY").write_text("DE")
    for args in (
        [guard, "check"],
        [guard, "hook"],
        [cli, "--version"],
        [guard, "desktop", "--", str(environment["desktop"]), "0"],
    ):
        assert subprocess.run(args, capture_output=True).returncode == 0
    report = subprocess.run([guard, "status"], capture_output=True, text=True)
    assert report.returncode == 0, report.stdout + report.stderr
    assert json.loads(report.stdout)["allowed_country"] == "DE"
    process = Process(42, 100, 1, str(environment["desktop"]))
    killer = Mock()
    kwargs = {"scanner": lambda *_: [process], "killer": killer, "notifier": Mock()}
    monitor.tick({process.executable}, **kwargs)
    killer.assert_not_called()
    changed = subprocess.run([guard, "set-country", "fr"], capture_output=True, text=True)
    assert changed.returncode == 0 and json.loads(changed.stdout)["allowed_country"] == "FR"
    # This second tick is in the same Python process: policy must not be cached.
    monitor.tick({process.executable}, **kwargs)
    killer.assert_called_once()
    for args in (
        [guard, "check"],
        [guard, "hook"],
        [cli, "--version"],
        [guard, "desktop", "--", str(environment["desktop"]), "0"],
    ):
        assert subprocess.run(args, capture_output=True).returncode == 2
    (environment["bins"] / "FAKE_COUNTRY").write_text("FR")
    assert subprocess.run([guard, "hook"], capture_output=True).returncode == 0
    installer.uninstall(paths)
