from claude_geo_guard.platforms.linux import systemd
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from claude_geo_guard import config, desktop_launcher
from claude_geo_guard.checker import Verdict
from claude_geo_guard.platforms.linux.desktop_entries import (
    discover,
    executable,
    intercept,
    restore,
    values,
)
from claude_geo_guard.storage import atomic_write, json_bytes


def test_metadata_actions_and_dbus(environment):
    paths = environment["paths"]
    entry = discover(paths)[0]
    changed, binaries, edits = intercept(entry, paths.bin / "claude-geo-guard")
    parsed = values(changed)
    original = values(entry.text)
    assert parsed["Desktop Entry"]["DBusActivatable"] == "false"
    assert parsed["Desktop Entry"]["Exec"].endswith(" %U")
    assert parsed["Desktop Action New"]["Exec"].endswith(" %u")
    for key in ("Name", "Icon", "Categories", "MimeType", "Actions"):
        assert parsed["Desktop Entry"][key] == original["Desktop Entry"][key]
    assert binaries == [str(environment["desktop"])]
    assert restore(changed, edits) == entry.text
    target = paths.data / "applications" / entry.id
    target.parent.mkdir(parents=True)
    target.write_text(changed)
    if shutil.which("desktop-file-validate"):
        subprocess.run(["desktop-file-validate", str(target)], check=True)


def test_user_override_and_multiple_installations(environment):
    paths = environment["paths"]
    user = paths.data / "applications" / environment["entry"].name
    user.parent.mkdir(parents=True)
    user.write_text(environment["entry"].read_text().replace("Name=Claude", "Name=Claude custom"))
    second = environment["entry"].with_name("second.desktop")
    second.write_text(environment["entry"].read_text())
    entries = discover(paths)
    assert len(entries) == 2
    assert entries[0].source == user


def test_autostart(environment):
    root = Path(os.environ["XDG_CONFIG_DIRS"]) / "autostart"
    root.mkdir(parents=True)
    (root / "claude.desktop").write_text(environment["entry"].read_text())
    assert any(e.autostart for e in discover(environment["paths"]))


def test_nested_desktop_id(environment):
    source = environment["entry"]
    nested = source.parent / "vendor" / source.name
    nested.parent.mkdir()
    source.rename(nested)
    entry = discover(environment["paths"])[0]
    assert entry.id == "vendor-" + source.name


@pytest.mark.parametrize("command", ["/missing/claude", "sh -c claude", "env claude", "claude %Z"])
def test_unsafe_or_missing_executable(command):
    with pytest.raises(ValueError):
        executable(command)


def test_script_launcher_refused(environment):
    with pytest.raises(ValueError, match="native ELF"):
        executable(str(environment["bins"] / "claude"))


def test_restore_preserves_user_metadata(environment):
    entry = discover(environment["paths"])[0]
    changed, _, edits = intercept(entry, environment["paths"].bin / "claude-geo-guard")
    changed = changed.replace("Icon=claude", "Icon=my-icon")
    assert "Icon=my-icon" in restore(changed, edits)
    with pytest.raises(ValueError):
        restore(changed.replace(" desktop --", " edited --"), edits)


@pytest.mark.parametrize("allowed,active", [(False, True), (True, False), (True, True)])
def test_desktop_launch_gate(environment, monkeypatch, allowed, active):
    paths = environment["paths"]
    atomic_write(paths.manifest, json_bytes({"binaries": [str(environment["desktop"])]}))
    atomic_write(paths.guard_config, json_bytes(config.DEFAULTS))
    monkeypatch.setattr(systemd, "active", lambda: active)
    monkeypatch.setattr(desktop_launcher, "check", lambda: Verdict(allowed, "test", {}))
    execute = Mock()
    monkeypatch.setattr(os, "execve", execute)
    args = [str(environment["desktop"]), "claude://hello world"]
    if allowed and active:
        desktop_launcher.launch(paths, args)
        execute.assert_called_once_with(args[0], args, dict(os.environ))
    else:
        with pytest.raises(ValueError):
            desktop_launcher.launch(paths, args)
        execute.assert_not_called()


def test_identical_action_exec_restores_by_section(environment):
    entry = discover(environment["paths"])[0]
    entry.text = entry.text.replace(" %u", " %U")
    changed, _, edits = intercept(entry, environment["paths"].bin / "claude-geo-guard")
    assert restore(changed, edits) == entry.text


def test_denial_explains_allowed_and_observed_countries(environment, monkeypatch):
    paths = environment["paths"]
    atomic_write(paths.manifest, json_bytes({"binaries": [str(environment["desktop"])]}))
    monkeypatch.setattr(systemd, "active", lambda: True)
    atomic_write(paths.guard_config, json_bytes(config.DEFAULTS))
    monkeypatch.setattr(
        desktop_launcher,
        "check",
        lambda: Verdict(False, "egress outside configured country GB", {4: "PL", 6: "GB"}, "GB"),
    )
    execute = Mock()
    monkeypatch.setattr(os, "execve", execute)
    with pytest.raises(ValueError) as error:
        desktop_launcher.launch(paths, [str(environment["desktop"])])
    assert "Allowed country: GB" in str(error.value)
    assert "Detected IPv4 country: PL" in str(error.value)
    assert "Detected IPv6 country: GB" in str(error.value)
    assert "Claude edge traces (plus ifconfig.io in full mode)" in str(error.value)
    execute.assert_not_called()


def test_duplicate_desktop_id_refused(environment):
    root = environment["entry"].parent
    (root / "vendor").mkdir()
    (root / "vendor/foo.desktop").write_text(environment["entry"].read_text())
    (root / "vendor-foo.desktop").write_text(environment["entry"].read_text())
    with pytest.raises(ValueError, match="ambiguous desktop-file ID"):
        discover(environment["paths"])


def test_hidden_entry_masks_system_entry(environment):
    paths = environment["paths"]
    entry = paths.data / "applications" / environment["entry"].name
    entry.parent.mkdir(parents=True)
    entry.write_text(environment["entry"].read_text() + "\n")
    entry.write_text(entry.read_text().replace("Type=Application", "Type=Application\nHidden=true"))
    assert discover(paths) == []


def test_crlf_entry_refused_without_edit(environment):
    entry = discover(environment["paths"])[0]
    entry.text = entry.text.replace("\n", "\r\n")
    original = entry.text
    with pytest.raises(ValueError, match="CRLF"):
        intercept(entry, environment["paths"].bin / "claude-geo-guard")
    assert entry.text == original


def test_dbus_only_entry_refused(environment):
    entry = discover(environment["paths"])[0]
    entry.text = "\n".join(line for line in entry.text.splitlines() if not line.startswith("Exec="))
    with pytest.raises(ValueError, match="no interceptable Exec"):
        intercept(entry, environment["paths"].bin / "claude-geo-guard")


def test_literal_percent_argument_preserved(environment):
    entry = discover(environment["paths"])[0]
    entry.text = entry.text.replace(" %U", " 100%% %U")
    changed, _, edits = intercept(entry, environment["paths"].bin / "claude-geo-guard")
    assert "100%% %U" in changed
    assert restore(changed, edits) == entry.text


def test_action_targeting_unrelated_program_refused(environment):
    entry = discover(environment["paths"])[0]
    entry.text = entry.text.replace(f'Exec="{environment["desktop"]}" %u', "Exec=/bin/true")
    with pytest.raises(ValueError, match="action executable differs"):
        intercept(entry, environment["paths"].bin / "claude-geo-guard")


def test_launch_starts_stopped_monitor(environment, monkeypatch):
    paths = environment["paths"]
    args = [str(environment["desktop"])]
    atomic_write(paths.manifest, json_bytes({"binaries": args}))
    atomic_write(paths.guard_config, json_bytes(dict(config.DEFAULTS, success_notifications=False)))
    start = Mock(return_value=True)
    monkeypatch.setattr(systemd, "best_effort", start)
    monkeypatch.setattr(systemd, "active", Mock(side_effect=[False, True]))
    monkeypatch.setattr(desktop_launcher, "check", lambda: Verdict(True, "verified", {}))
    execute = Mock()
    monkeypatch.setattr(os, "execve", execute)
    desktop_launcher.launch(paths, args)
    start.assert_called_once_with("start", systemd.UNIT)
    execute.assert_called_once()
