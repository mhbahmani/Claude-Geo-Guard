import copy
import json
from unittest.mock import patch

import pytest

from claude_geo_guard import settings_manager as sm
from claude_geo_guard.storage import Transaction, atomic_write

COMMAND = "/a path/claude-geo-guard"


@pytest.mark.parametrize(
    "original",
    [
        {},
        {"hooks": {}},
        {"hooks": {"PreToolUse": []}},
        {
            "env": {"X": "y"},
            "permissions": {"allow": ["Read"]},
            "model": "custom",
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {"type": "command", "command": "first"},
                            {"type": "command", "command": "second"},
                        ],
                    }
                ],
                "Stop": [{"hooks": [{"command": "third", "type": "command"}]}],
            },
        },
    ],
)
def test_roundtrip_preserves_all(original):
    saved = copy.deepcopy(original)
    owned = sm.owned_groups(COMMAND)
    installed = sm.merge(original, owned, COMMAND)
    assert original == saved
    assert sm.merge(installed, owned, COMMAND) == installed
    assert sm.remove(installed, owned, COMMAND, saved) == saved
    assert sm.remove(saved, owned, COMMAND, saved) == saved
    for event, groups in saved.get("hooks", {}).items():
        assert installed["hooks"][event][: len(groups)] == groups


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{",
        b"[]",
        b'{"hooks":[]}',
        b'{"hooks":{"A":{}}}',
        b'{"hooks":{"A":[{}]}}',
        b'{"hooks":{"A":[{"hooks":[4]}]}}',
        b'{"x":1,"x":2}',
    ],
)
def test_invalid_settings(raw):
    with pytest.raises(ValueError):
        sm.parse(raw)


def test_uninstall_preserves_new_preferences():
    groups = sm.owned_groups(COMMAND)
    data = sm.merge({}, groups, COMMAND)
    data["model"] = "new"
    assert sm.remove(data, groups, COMMAND, {}) == {"model": "new"}


@pytest.mark.parametrize("edit", ["handler", "combined", "matcher"])
def test_modified_owned_group_conflicts(edit):
    groups = sm.owned_groups(COMMAND)
    data = sm.merge({}, groups, COMMAND)
    group = data["hooks"]["PreToolUse"][0]
    if edit == "handler":
        group["hooks"][0]["timeout"] = 90
    elif edit == "combined":
        group["hooks"].append({"command": "mine"})
    else:
        group["matcher"] = "Bash"
    with pytest.raises(ValueError):
        sm.remove(data, groups, COMMAND, {})
    with pytest.raises(ValueError):
        sm.merge(data, groups, COMMAND)


def test_atomic_failure_retains_original(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"model":"original"}')
    with patch("os.replace", side_effect=OSError("disk failure")), pytest.raises(OSError):
        atomic_write(path, b"replacement")
    assert json.loads(path.read_text()) == {"model": "original"}
    assert not list(tmp_path.glob(".guard-*"))


def test_journal_rollback_and_concurrent_edit(tmp_path):
    path = tmp_path / "settings"
    path.write_bytes(b"before")
    tx = Transaction(tmp_path / "backup.json")
    tx.write(path, b"after")
    tx.rollback()
    assert path.read_bytes() == b"before"
    tx = Transaction(tmp_path / "backup2.json")
    tx.write(path, b"after")
    path.write_bytes(b"user edit")
    assert tx.rollback() == [str(path)]
    assert path.read_bytes() == b"user edit"


def test_symlink_settings_refused(tmp_path):
    original = tmp_path / "original"
    original.write_text("{}")
    link = tmp_path / "settings"
    link.symlink_to(original)
    with pytest.raises(ValueError):
        atomic_write(link, b"bad")
    assert original.read_text() == "{}"


@pytest.mark.parametrize("edit", ["command", "event"])
def test_changed_command_or_moved_event_conflicts(edit):
    groups = sm.owned_groups(COMMAND)
    data = sm.merge({}, groups, COMMAND)
    if edit == "command":
        data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = "/new/command"
    else:
        data["hooks"]["Stop"] = data["hooks"].pop("PreToolUse")
    with pytest.raises(ValueError):
        sm.remove(data, groups, COMMAND, {})


@pytest.mark.parametrize(
    "restriction",
    [
        {"disableAllHooks": True},
        {"allowManagedHooksOnly": True},
        {"strictPluginOnlyCustomization": True},
        {"strictPluginOnlyCustomization": ["hooks"]},
    ],
)
def test_disabled_hooks_refused(restriction):
    with pytest.raises(ValueError, match="disable"):
        sm.merge(restriction, sm.owned_groups(COMMAND), COMMAND)


def test_nonstandard_json_constant_refused():
    with pytest.raises(ValueError):
        sm.parse(b'{"model":NaN}')
