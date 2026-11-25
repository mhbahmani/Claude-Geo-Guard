import json
import logging
import subprocess
from unittest.mock import Mock

import pytest

from claude_geo_guard import checker, config, installer, monitor
from claude_geo_guard.storage import atomic_write, json_bytes


def save(paths, **changes):
    values = dict(config.DEFAULTS, **changes)
    atomic_write(paths.guard_config, json_bytes(values))
    return values


def test_user_config_is_separate_from_installer_state(environment):
    paths = environment["paths"]
    installer.install(
        paths, installer.preflight(paths, country="DE", timezone="UTC", interval=20, grace=2)
    )
    assert config.load(paths) == dict(
        config.DEFAULTS, allowed_country="DE", timezone="UTC", interval=20, grace=2
    )
    manifest = installer.load_manifest(paths)
    assert not (config.DEFAULTS.keys() & manifest.keys())
    assert paths.guard_config == paths.config / "claude-geo-guard/config.json"
    assert set(json.loads(paths.guard_config.read_bytes())) == set(config.DEFAULTS)
    assert paths.guard_config.stat().st_mode & 0o777 == 0o600
    assert "--interval" not in (paths.config / "systemd/user/claude-geo-guard.service").read_text()
    installer.uninstall(paths)
    assert not paths.guard_config.exists()


@pytest.mark.parametrize(
    "key,value",
    [
        ("enabled", "false"),
        ("enabled", 0),
        ("interval", 0),
        ("interval", 301),
        ("interval", 10**400),
        ("interval", True),
        ("interval", "15"),
        ("grace", -1),
        ("grace", 31),
        ("grace", False),
        ("grace", float("inf")),
        ("interval", float("nan")),
        ("timezone", "bad/zone"),
        ("allowed_country", "UK"),
    ],
)
def test_bad_manual_setting_denies_without_network(isolated, key, value):
    save(isolated, **{key: value})
    request = Mock()
    assert not checker.check(request=request, environ={}).allowed
    request.assert_not_called()


@pytest.mark.parametrize(
    "raw", [b"{", b"[]", b"{}", b'{"allowed_country":"GB","allowed_country":"DE"}', b"\xff"]
)
def test_invalid_config_rejected_without_reset(environment, raw):
    paths = environment["paths"]
    atomic_write(paths.guard_config, raw)
    with pytest.raises(ValueError):
        installer.preflight(paths)
    assert paths.guard_config.read_bytes() == raw
    assert not paths.manifest.exists()


def test_unknown_key_and_symlink_refused(isolated):
    save(isolated, typo="value")
    with pytest.raises(ValueError, match="unknown keys"):
        config.load(isolated)
    isolated.guard_config.unlink()
    target = isolated.home / "preferences.json"
    target.write_bytes(json_bytes(config.DEFAULTS))
    isolated.guard_config.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        config.load(isolated)


@pytest.mark.parametrize("reinstall", [False, True])
def test_edited_config_survives_uninstall(environment, reinstall):
    paths = environment["paths"]
    installer.install(paths, installer.preflight(paths))
    edited = save(paths, allowed_country="FR", timezone="UTC", interval=22, grace=2)
    if reinstall:
        installer.install(paths, installer.preflight(paths))
    assert config.load(paths) == edited
    installer.uninstall(paths)
    assert config.load(paths) == edited
    installer.uninstall(paths)
    assert config.load(paths) == edited


def test_preexisting_config_formatting_survives_install_and_uninstall(environment):
    paths = environment["paths"]
    raw = b'  {"grace":2, "interval":20, "timezone":null, "allowed_country":"de"}\n'
    atomic_write(paths.guard_config, raw)
    installer.install(paths, installer.preflight(paths))
    assert paths.guard_config.read_bytes() == raw
    installer.uninstall(paths)
    assert paths.guard_config.read_bytes() == raw


def test_config_edit_after_plan_aborts(environment):
    paths = environment["paths"]
    plan = installer.preflight(paths)
    raw = json_bytes(save(paths, allowed_country="FR"))
    with pytest.raises(ValueError, match="changed since inspection"):
        installer.install(paths, plan)
    assert paths.guard_config.read_bytes() == raw
    assert not paths.manifest.exists()


@pytest.mark.parametrize(
    "timings, expected",
    [
        ({}, (15, 5)),
        ({"interval": 20}, (20, 5)),
        ({"grace": 0}, (15, 0)),
    ],
)
def test_recover_missing_installed_config(environment, timings, expected):
    paths = environment["paths"]
    installer.install(paths, installer.preflight(paths))
    paths.guard_config.unlink()
    assert not checker.check(environ={}).allowed
    with pytest.raises(ValueError, match="--country CODE and --timezone"):
        installer.preflight(paths)
    installer.install(
        paths, installer.preflight(paths, country="GB", timezone="Europe/London", **timings)
    )
    assert config.load(paths) == dict(
        config.DEFAULTS, timezone="Europe/London", interval=expected[0], grace=expected[1]
    )


def test_direct_edit_changes_installed_check_and_hook(environment, monkeypatch):
    paths = environment["paths"]
    installer.install(paths, installer.preflight(paths))
    guard = str(paths.bin / "claude-geo-guard")
    (environment["bins"] / "FAKE_COUNTRY").write_text("DE")
    assert subprocess.run([guard, "hook"], capture_output=True).returncode == 2
    save(paths, allowed_country="DE")
    for command in ("check", "hook"):
        result = subprocess.run([guard, command], capture_output=True)
        assert result.returncode == 0, result.stderr
    atomic_write(paths.guard_config, b"{")
    assert subprocess.run([guard, "hook"], capture_output=True).returncode == 2
    installer.uninstall(paths)
    assert paths.guard_config.read_bytes() == b"{"  # Even malformed user edits are retained.


@pytest.mark.parametrize(
    "initially_enabled, invalid_second", [(True, False), (True, True), (False, False)]
)
def test_monitor_reloads_manual_edits_and_fails_closed(
    environment, monkeypatch, caplog, initially_enabled, invalid_second
):
    paths = environment["paths"]
    installer.install(paths, installer.preflight(paths))
    save(paths, enabled=initially_enabled)
    waits, ticks = [], []

    class Stop:
        stopped = False

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, seconds):
            waits.append(seconds)
            if len(waits) == 1:
                if invalid_second:
                    atomic_write(paths.guard_config, b"{")
                else:
                    save(paths, allowed_country="FR", interval=8, grace=1)
            else:
                self.set()
            return self.stopped

    monkeypatch.setattr(monitor.threading, "Event", Stop)
    monkeypatch.setattr(monitor.signal, "signal", lambda *_: None)

    def tick(binaries, previous, grace, stop, **kwargs):
        ticks.append(grace)
        if invalid_second and len(ticks) == 2:
            assert not kwargs["checker"]().allowed
        return []

    monkeypatch.setattr(monitor, "tick", tick)
    caplog.set_level(logging.ERROR)
    monitor.run(paths)
    assert ticks == ([5] if initially_enabled else []) + [0 if invalid_second else 1]
    assert waits == ([15, 5] if invalid_second else [15, 8])
    if invalid_second:
        assert "configuration_invalid" in caplog.text
