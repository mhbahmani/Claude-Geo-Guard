from claude_geo_guard.platforms.linux import systemd
import json
import os
import shutil
import subprocess
from unittest.mock import Mock
from zoneinfo import ZoneInfoNotFoundError

import pytest

from claude_geo_guard import cli_launcher, config, desktop_launcher, installer
from claude_geo_guard.checker import Verdict
from claude_geo_guard.cli import main
from claude_geo_guard.storage import atomic_write, json_bytes
from claude_geo_guard.timezone import launch_environment, load_timezone, normalize_timezone
from conftest import script


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("inherit", None),
        ("UTC", "UTC"),
        (" Europe/London ", "Europe/London"),
        ("Asia/Tehran", "Asia/Tehran"),
        ("America/New_York", "America/New_York"),
    ],
)
def test_valid_timezone(value, expected):
    assert normalize_timezone(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "Not_A_Timezone",
        "europe/london",
        "UTC+01:00",
        "/etc/localtime",
        "../UTC",
        "Europe/../UTC",
        "localtime",
        "posixrules",
        "right/UTC",
        "posix/UTC",
        1,
        [],
        "Missing/Zone",
    ],
)
def test_invalid_timezone(value):
    with pytest.raises(ValueError, match="timezone|IANA"):
        normalize_timezone(value)


def test_missing_timezone_database_fails_clearly(monkeypatch):
    monkeypatch.setattr(
        "claude_geo_guard.timezone.ZoneInfo",
        Mock(no_cache=Mock(side_effect=ZoneInfoNotFoundError())),
    )
    with pytest.raises(ValueError, match="install tzdata"):
        normalize_timezone("Europe/London")


def test_environment_inherited_or_overridden_without_mutating_parent(isolated, monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Tehran")
    monkeypatch.setenv("TZDIR", "/custom/timezones")
    monkeypatch.setenv("UNRELATED", "preserved")
    before = dict(os.environ)
    assert load_timezone(isolated) is None
    assert launch_environment(isolated) == before
    atomic_write(isolated.guard_config, json_bytes(dict(config.DEFAULTS, timezone="Europe/London")))
    result = launch_environment(isolated)
    assert result["TZ"] == "Europe/London"
    assert "TZDIR" not in result
    assert result["UNRELATED"] == "preserved"
    assert dict(os.environ) == before
    atomic_write(isolated.guard_config, json_bytes(dict(config.DEFAULTS, timezone=None)))
    assert launch_environment(isolated) == before


def test_unset_environment_stays_unset(isolated, monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    assert "TZ" not in launch_environment(isolated)


def test_install_change_reinstall_clear_and_uninstall(environment):
    paths = environment["paths"]
    plan = installer.preflight(paths, country="GB", timezone="Europe/London")
    assert installer.summary(plan)["timezone"] == "Europe/London"
    assert not paths.manifest.exists()
    installer.install(paths, plan)
    before = installer.load_manifest(paths)
    settings = paths.settings.read_bytes()
    assert main(["set-timezone", "Europe/Berlin"]) == 0
    after = installer.load_manifest(paths)
    assert {k: v for k, v in before.items() if k != "timezone"} == {
        k: v for k, v in after.items() if k != "timezone"
    }
    assert load_timezone(paths) == "Europe/Berlin"
    installer.set_country(paths, "DE")
    installer.install(paths, installer.preflight(paths))
    assert load_timezone(paths) == "Europe/Berlin"
    assert paths.settings.read_bytes() == settings
    installer.install(paths, installer.preflight(paths, timezone="inherit"))
    assert load_timezone(paths) is None
    installer.set_timezone(paths, "UTC")
    assert main(["set-timezone", "inherit"]) == 0
    assert load_timezone(paths) is None
    installer.uninstall(paths)
    assert not paths.manifest.exists()
    assert not paths.settings.exists()


def test_default_install_preserves_environment(environment):
    paths = environment["paths"]
    assert installer.preflight(paths)["timezone"] is None


def test_invalid_and_failed_update_preserves_manifest(environment, monkeypatch):
    paths = environment["paths"]
    with pytest.raises(ValueError):
        installer.preflight(paths, timezone="Missing/Zone")
    assert not paths.manifest.exists()
    installer.install(paths, installer.preflight(paths, timezone="UTC"))
    before = paths.guard_config.read_bytes()
    assert main(["set-timezone", "Missing/Zone"]) == 2
    assert paths.guard_config.read_bytes() == before
    monkeypatch.setattr("os.replace", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        installer.set_timezone(paths, "Europe/London")
    assert paths.guard_config.read_bytes() == before


def test_setting_requires_installation(isolated):
    with pytest.raises(ValueError, match="install the guard first"):
        installer.set_timezone(isolated, "UTC")


@pytest.mark.parametrize("launcher", ["cli", "desktop"])
def test_invalid_saved_timezone_denies_launch(environment, monkeypatch, launcher):
    paths = environment["paths"]
    atomic_write(
        paths.manifest,
        json_bytes({"binaries": [str(environment["desktop"])]}),
    )
    atomic_write(paths.guard_config, json_bytes(dict(config.DEFAULTS, timezone="Missing/Zone")))
    execute = Mock()
    monkeypatch.setattr(os, "execve", execute)
    monkeypatch.setattr(cli_launcher, "check", lambda: Verdict(True, "allowed", {}))
    monkeypatch.setattr(desktop_launcher, "check", lambda: Verdict(True, "allowed", {}))
    monkeypatch.setattr(systemd, "active", lambda: True)
    with pytest.raises(ValueError, match="timezone"):
        if launcher == "cli":
            cli_launcher.launch(paths, [])
        else:
            desktop_launcher.launch(paths, [str(environment["desktop"])])
    execute.assert_not_called()


def test_real_cli_and_native_desktop_receive_timezone(environment, monkeypatch):
    paths = environment["paths"]
    # Native date observes libc's TZ handling, including daylight-saving transitions.
    shutil.copy2("/bin/date", environment["desktop"])
    script(
        environment["bins"] / "claude",
        """import os, sys
if sys.argv[1:] == ["--version"]:
    print("2.1.295 (Claude Code)")
else:
    print(os.environ.get("TZ", "unset"))
""",
    )
    monkeypatch.setenv("TZ", "UTC")
    assert main(["install", "--yes", "--timezone", "Europe/London"]) == 0
    guard = str(paths.bin / "claude-geo-guard")
    cli = str(paths.bin / "claude")
    result = subprocess.run([cli], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == "Europe/London"
    for date, expected in [
        ("2026-01-01T12:00:00Z", "GMT +0000"),
        ("2026-07-01T12:00:00Z", "BST +0100"),
    ]:
        result = subprocess.run(
            [guard, "desktop", "--", str(environment["desktop"]), f"--date={date}", "+%Z %z"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected
    monkeypatch.setenv("PATH", f"{paths.bin}:{os.environ['PATH']}")
    status = subprocess.run([guard, "doctor", "--json"], capture_output=True, text=True)
    assert status.returncode == 0, status.stdout + status.stderr
    assert json.loads(status.stdout)["timezone"] == "Europe/London"
    changed = subprocess.run([guard, "set-timezone", "Asia/Tehran"], capture_output=True, text=True)
    assert changed.returncode == 0, changed.stderr
    assert subprocess.check_output([cli], text=True).strip() == "Asia/Tehran"
    subprocess.run([guard, "set-timezone", "inherit"], capture_output=True, check=True)
    assert subprocess.check_output([cli], text=True).strip() == "UTC"
    assert os.environ["TZ"] == "UTC"
    installer.uninstall(paths)
