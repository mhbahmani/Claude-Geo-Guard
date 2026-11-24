from claude_geo_guard import installer


def test_names_are_consistent(environment):
    paths = environment["paths"]
    assert paths.root.name == "claude-geo-guard"
    assert paths.manifest.parent.name == "claude-geo-guard"
    installer.install(paths, installer.preflight(paths, country="DE"))
    assert (paths.bin / "claude-geo-guard").exists()
    assert (paths.root / "lib/claude_geo_guard/cli.py").exists()
    assert (paths.config / "systemd/user/claude-geo-guard.service").exists()
    assert "Claude Geo Guard" in paths.settings.read_text()
    assert "claude-geo-guard" in (paths.home / ".bashrc").read_text()
    installer.uninstall(paths)
    assert not paths.manifest.exists()
