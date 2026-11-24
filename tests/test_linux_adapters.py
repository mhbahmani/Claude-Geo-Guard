import subprocess
from unittest.mock import Mock

from claude_geo_guard.platforms.linux import systemd
from claude_geo_guard.paths import Paths


def test_invalid_xdg_home_uses_spec_defaults(isolated, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "relative")
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    paths = Paths.current()
    assert paths.data == isolated.home / ".local/share"
    assert paths.config == isolated.home / ".config"


def test_systemd_path_and_failure_policy():
    unit = systemd.unit("/home/test path/%user/$money/guard")
    assert 'ExecStart="/home/test path/%%user/$$money/guard" monitor' in unit
    assert "Restart=on-failure" in unit and "StartLimitBurst=3" in unit
    assert "WantedBy=default.target" in unit


def test_user_manager_timeout_recovery(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("systemctl", 10))
    )
    assert not systemd.available()
    assert not systemd.active()
    assert not systemd.best_effort("stop", systemd.UNIT)
