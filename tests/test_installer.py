import json

import pytest

from claude_geo_guard import installer
from claude_geo_guard.platforms.linux import systemd
from claude_geo_guard.storage import read


def files(home):
    return {
        str(p.relative_to(home)): p.read_bytes()
        for p in home.rglob("*")
        if p.is_file()
        and ".local/state/claude-geo-guard" not in str(p)
        and p.name != "systemd-state"
    }


def test_dry_plan_no_writes(environment):
    paths = environment["paths"]
    before = files(paths.home)
    plan = installer.preflight(paths)
    assert plan["desktops"] and plan["groups"]
    assert files(paths.home) == before


def test_install_reinstall_uninstall(environment):
    paths = environment["paths"]
    paths.settings.parent.mkdir()
    raw = b'{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"mine"}]}]}, "model":"custom"}'
    paths.settings.write_bytes(raw)
    (paths.home / ".bashrc").write_text("# my shell\n")
    before = files(paths.home)
    installer.install(paths, installer.preflight(paths))
    first = json.loads(paths.settings.read_text())
    installer.install(paths, installer.preflight(paths))
    assert json.loads(paths.settings.read_text()) == first
    assert (paths.home / ".bashrc").read_text().count("# >>> claude-geo-guard >>>") == 1
    assert systemd.active()
    installer.uninstall(paths)
    assert files(paths.home) == before
    assert list((paths.manifest.parent / "backups").glob("*.json"))
    assert installer.uninstall(paths)["uninstalled"]


def test_existing_user_override_roundtrip(environment):
    paths = environment["paths"]
    target = paths.data / "applications" / environment["entry"].name
    target.parent.mkdir(parents=True)
    original = environment["entry"].read_text().replace("Icon=claude", "Icon=my-icon")
    target.write_text(original)
    installer.install(paths, installer.preflight(paths))
    installer.uninstall(paths)
    assert target.read_text() == original


def test_reinstall_after_desktop_launcher_regenerated(environment):
    paths = environment["paths"]
    target = paths.data / "applications" / environment["entry"].name
    target.parent.mkdir(parents=True)
    original = environment["entry"].read_bytes()
    target.write_bytes(original)
    installer.install(paths, installer.preflight(paths))
    guarded = target.read_bytes()

    target.write_bytes(original.replace(b" %U", b" --custom %U"))
    with pytest.raises(ValueError, match="desktop launch line edited") as conflict:
        installer.preflight(paths)
    assert str(target) in str(conflict.value)
    assert b"--custom" in target.read_bytes()

    target.write_bytes(original)
    paths.guard_config.unlink()
    installer.install(paths, installer.preflight(paths, country="GB", timezone="inherit"))
    assert target.read_bytes() == guarded
    installer.uninstall(paths)
    assert target.read_bytes() == original


def test_new_user_edits_preserved(environment):
    paths = environment["paths"]
    installer.install(paths, installer.preflight(paths))
    settings = json.loads(paths.settings.read_text())
    settings["model"] = "new-user-choice"
    paths.settings.write_text(json.dumps(settings))
    rc = paths.home / ".bashrc"
    rc.write_text(rc.read_text() + "# user edit\n")
    desktop = paths.data / "applications" / environment["entry"].name
    desktop.write_text(desktop.read_text().replace("Icon=claude", "Icon=my-icon"))
    installer.uninstall(paths)
    assert json.loads(paths.settings.read_text()) == {"model": "new-user-choice"}
    assert rc.read_text() == "# user edit\n"
    assert "Icon=my-icon" in desktop.read_text() and "guard" not in desktop.read_text()


def test_service_failure_rolls_back(environment, monkeypatch):
    paths = environment["paths"]
    paths.settings.parent.mkdir()
    paths.settings.write_text('{"model":"original"}')
    before = files(paths.home)
    monkeypatch.setenv("FAKE_SYSTEMD_FAIL", "1")
    with pytest.raises(Exception):
        installer.install(paths, installer.preflight(paths))
    assert files(paths.home) == before
    assert not paths.manifest.exists()


def test_conflict_uninstall_leaves_installation(environment):
    paths = environment["paths"]
    installer.install(paths, installer.preflight(paths))
    data = json.loads(paths.settings.read_text())
    data["hooks"]["PreToolUse"][0]["hooks"].append({"command": "mine"})
    paths.settings.write_text(json.dumps(data))
    before = files(paths.home)
    with pytest.raises(ValueError, match="combined"):
        installer.uninstall(paths)
    assert files(paths.home) == before
    assert systemd.active()


def test_malformed_settings_no_mutations(environment):
    paths = environment["paths"]
    paths.settings.parent.mkdir()
    paths.settings.write_text("{")
    before = files(paths.home)
    with pytest.raises(ValueError):
        installer.preflight(paths)
    assert files(paths.home) == before


def test_package_executable_move_update(environment):
    paths = environment["paths"]
    installer.install(paths, installer.preflight(paths))
    old = environment["desktop"]
    new = old.with_name("desktop-updated")
    old.rename(new)
    entry = environment["entry"]
    entry.write_text(entry.read_text().replace(str(old), str(new)))
    installer.install(paths, installer.preflight(paths))
    manifest = installer.load_manifest(paths)
    assert str(new) in manifest["binaries"]
    assert str(old) not in manifest["binaries"]
    installer.uninstall(paths)
    assert new.exists() and str(new) in entry.read_text()


def test_no_user_manager_refuses_desktop(environment, monkeypatch):
    monkeypatch.setattr(systemd, "available", lambda: False)
    with pytest.raises(ValueError, match="systemd user"):
        installer.preflight(environment["paths"])
    assert installer.preflight(environment["paths"], skip_desktop=True)["desktops"] == []


def test_unknown_managed_file_preserved(environment):
    paths = environment["paths"]
    target = paths.bin / "claude-geo-guard"
    target.parent.mkdir(parents=True)
    target.write_text("user data")
    with pytest.raises(ValueError, match="unmanaged"):
        installer.install(paths, installer.preflight(paths))
    assert target.read_text() == "user data"
    assert not paths.settings.exists()


def test_changed_settings_between_plan_and_install(environment):
    paths = environment["paths"]
    plan = installer.preflight(paths)
    paths.settings.parent.mkdir()
    paths.settings.write_text('{"model":"changed"}')
    with pytest.raises(ValueError, match="changed since"):
        installer.install(paths, plan)
    assert read(paths.settings) == b'{"model":"changed"}'


def test_bash_zsh_path_syntax(environment):
    paths = environment["paths"]
    installer.install(paths, installer.preflight(paths))
    import subprocess
    import shutil

    for shell, rc in (("bash", ".bashrc"), ("zsh", ".zshrc")):
        if shutil.which(shell):
            subprocess.run([shell, "-n", str(paths.home / rc)], check=True)
    installer.uninstall(paths)


def test_manager_disappears_during_failure_still_restores_files(environment, monkeypatch):
    paths = environment["paths"]
    plan = installer.preflight(paths)
    before = files(paths.home)
    original_run = systemd.run

    def run(*args, **kwargs):
        if args[0] in {"restart", "stop", "disable", "daemon-reload"}:
            raise TimeoutError("user manager vanished")
        return original_run(*args, **kwargs)

    monkeypatch.setattr(systemd, "run", run)
    with pytest.raises(TimeoutError):
        installer.install(paths, plan)
    assert files(paths.home) == before
    assert not paths.manifest.exists()


def test_existing_bash_login_profile_gets_reversible_path(environment):
    paths = environment["paths"]
    profile = paths.home / ".bash_profile"
    profile.write_text("# custom login, does not source .profile\n")
    before = profile.read_text()
    installer.install(paths, installer.preflight(paths))
    assert str(paths.bin) in profile.read_text()
    installer.uninstall(paths)
    assert profile.read_text() == before


def test_unsupported_distro_refused(environment, monkeypatch):
    monkeypatch.setattr(
        "platform.freedesktop_os_release", lambda: {"ID": "debian", "VERSION_ID": "11"}
    )
    with pytest.raises(ValueError, match="supported distributions"):
        installer.preflight(environment["paths"])


def test_custom_config_dir_refused(environment, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/custom/config")
    with pytest.raises(ValueError, match="CLAUDE_CONFIG_DIR"):
        installer.preflight(environment["paths"])


def test_unowned_existing_guard_hook_not_adopted(environment):
    from claude_geo_guard.settings_manager import merge, owned_groups

    paths = environment["paths"]
    command = paths.bin / "claude-geo-guard"
    data = merge({}, owned_groups(command), command)
    paths.settings.parent.mkdir()
    paths.settings.write_text(json.dumps(data))
    before = paths.settings.read_bytes()
    with pytest.raises(ValueError, match="ownership manifest"):
        installer.preflight(paths)
    assert paths.settings.read_bytes() == before


def test_unowned_path_block_preserved(environment):
    from claude_geo_guard.shell import block

    paths = environment["paths"]
    rc = paths.home / ".bashrc"
    rc.write_text(block(paths.bin))
    before = files(paths.home)
    with pytest.raises(ValueError, match="unowned guard PATH"):
        installer.install(paths, installer.preflight(paths))
    assert files(paths.home) == before


@pytest.mark.parametrize("user_override", [False, True])
def test_custom_desktop_edits_survive_repeated_reinstall(environment, user_override):
    paths = environment["paths"]
    target = paths.data / "applications" / environment["entry"].name
    if user_override:
        target.parent.mkdir(parents=True)
        target.write_bytes(environment["entry"].read_bytes())
    installer.install(paths, installer.preflight(paths))
    target.write_text(target.read_text().replace("Icon=claude", "Icon=user-new-icon"))
    for _ in range(2):
        installer.install(paths, installer.preflight(paths))
        assert "Icon=user-new-icon" in target.read_text()
    installer.uninstall(paths)
    assert "Icon=user-new-icon" in target.read_text()
    assert " desktop -- " not in target.read_text()


def test_invalid_crlf_entry_preserved(environment):
    paths = environment["paths"]
    target = paths.data / "applications" / environment["entry"].name
    target.parent.mkdir(parents=True)
    original = environment["entry"].read_bytes().replace(b"\n", b"\r\n")
    target.write_bytes(original)
    with pytest.raises(ValueError, match="CRLF"):
        installer.preflight(paths)
    assert target.read_bytes() == original


def test_install_mode_persists_and_can_be_disabled(environment):
    from claude_geo_guard import config

    paths = environment["paths"]
    installer.install(paths, installer.preflight(paths, skip_desktop=True, split_tunnel=True))
    assert installer.preflight(paths, skip_desktop=True)["split_tunnel"]
    installer.install(paths, installer.preflight(paths, skip_desktop=True, split_tunnel=False))
    assert not config.load(paths)["split_tunnel"]
