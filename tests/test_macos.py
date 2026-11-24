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
        installer.preflight(paths)
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
