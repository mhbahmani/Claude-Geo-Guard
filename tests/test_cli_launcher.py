import json
import os
import shlex
import subprocess
from unittest.mock import Mock

import pytest

from claude_geo_guard import cli_launcher as cl
from claude_geo_guard import settings_manager
from claude_geo_guard.checker import Verdict
from conftest import script


def register_hooks(paths, **extra):
    guard = paths.bin / "claude-geo-guard"
    data = settings_manager.merge(extra, settings_manager.owned_groups(guard), guard)
    paths.settings.parent.mkdir(parents=True, exist_ok=True)
    paths.settings.write_text(json.dumps(data))
    return data


@pytest.fixture
def hooks(environment):
    return register_hooks(environment["paths"])


def test_discovery_recursion_and_explicit(environment):
    paths, bins = environment["paths"], environment["bins"]
    script(paths.bin / "claude", "# CLAUDE_GEO_GUARD_WRAPPER\n")
    assert cl.discover(paths.bin, path=f"{paths.bin}:{bins}") == bins / "claude"
    assert cl.discover(paths.bin, str(bins / "claude")) == bins / "claude"
    with pytest.raises(ValueError):
        cl.discover(paths.bin, str(paths.bin / "claude"))


def test_symlink_recursion(environment, tmp_path):
    paths = environment["paths"]
    script(paths.bin / "claude", "# CLAUDE_GEO_GUARD_WRAPPER\n")
    link = tmp_path / "claude"
    link.symlink_to(paths.bin / "claude")
    with pytest.raises(ValueError):
        cl.discover(paths.bin, str(link))


@pytest.mark.parametrize(
    "text,message",
    [
        ("2.1.294 (Claude Code)", "Detected Claude Code CLI 2.1.294"),
        ("1.0.0", "Detected Claude Code CLI 1.0.0"),
        ("garbage", "Cannot verify"),
        ("2.1.295-beta", "Cannot verify"),
        ("", "Cannot verify"),
    ],
)
def test_unsupported_version(environment, text, message):
    binary = script(environment["bins"] / "old", f"print({text!r})\n")
    with pytest.raises(ValueError) as error:
        cl.version(binary)
    assert message in str(error.value)
    assert str(binary) in str(error.value)
    assert text in str(error.value) or message.startswith("Detected")


@pytest.mark.parametrize("text", ["2.1.295 (Claude Code)", "2.1.300", "2.2.0 (Claude Code)"])
def test_supported_version(environment, text):
    binary = script(environment["bins"] / "claude", f"print({text!r})\n")
    assert cl.version(binary) == text


def test_version_timeout_blocks_launch(environment, hooks, monkeypatch):
    binary = environment["bins"] / "claude"
    command = [str(binary), "--version"]
    probe = Mock(side_effect=subprocess.TimeoutExpired(command, 30))
    monkeypatch.setattr(cl, "check", lambda: Verdict(True, "GB", {4: "GB"}))
    monkeypatch.setattr(subprocess, "run", probe)
    execute = Mock()
    monkeypatch.setattr(os, "execve", execute)
    with pytest.raises(ValueError, match="timed out after 30 seconds") as error:
        cl.launch(environment["paths"], [])
    assert shlex.join(command) in str(error.value)
    assert probe.call_args.kwargs["timeout"] == 30
    execute.assert_not_called()


def test_launch_argv(environment, hooks, monkeypatch):
    monkeypatch.setattr(cl, "check", lambda: Verdict(True, "GB", {4: "GB"}))
    execute = Mock()
    monkeypatch.setattr(os, "execve", execute)
    args = ["arg with spaces", "'quote'", "$(touch bad)", "--flag"]
    cl.launch(environment["paths"], args)
    binary = str(environment["bins"] / "claude")
    execute.assert_called_once_with(binary, [binary, *args], dict(os.environ))


def test_deny_never_executes(environment, hooks, monkeypatch):
    monkeypatch.setattr(cl, "check", lambda: Verdict(False, "denied", {}))
    execute = Mock()
    monkeypatch.setattr(os, "execve", execute)
    with pytest.raises(ValueError):
        cl.launch(environment["paths"], [])
    execute.assert_not_called()


def test_version_probe_runs_before_final_egress_check(environment, hooks, monkeypatch):
    order = []
    monkeypatch.setattr(cl, "version", lambda binary: order.append("version"))
    monkeypatch.setattr(cl, "check", lambda: order.append("check") or Verdict(True, "GB", {}))
    monkeypatch.setattr(os, "execve", Mock())
    cl.launch(environment["paths"], [])
    assert order == ["version", "check"]


@pytest.mark.parametrize(
    "args,environ,message",
    [
        (["--bare", "-p", "hi"], {}, "--bare starts Claude Code in bare mode"),
        (["-p", "hi", "--safe-mode"], {}, "--safe-mode"),
        (["--restricted"], {}, "--restricted"),
        ([], {"CLAUDE_CODE_SIMPLE": "1"}, "CLAUDE_CODE_SIMPLE"),
        ([], {"CLAUDE_CODE_SAFE_MODE": "true"}, "CLAUDE_CODE_SAFE_MODE"),
        (["--setting-sources", "project,local"], {}, "--setting-sources without user"),
        (["--setting-sources=project"], {}, "--setting-sources without user"),
        (["--settings", '{"disableAllHooks": true}'], {}, "sets disableAllHooks"),
        (["--settings", "/missing/settings.json"], {}, "cannot read --settings"),
        ([], {"CLAUDE_CONFIG_DIR": "/elsewhere"}, "hooks are missing or edited in /elsewhere"),
    ],
)
def test_invocations_without_hooks_are_refused(environment, hooks, args, environ, message):
    with pytest.raises(ValueError, match=message.replace("(", r"\(")):
        cl.hook_coverage(environment["paths"], args, environ)


@pytest.mark.parametrize(
    "args",
    [
        ["-p", "explain the --bare flag"],
        ["-p", "--", "--bare"],
        ["--append-system-prompt", "--bare", "hello"],
        ["--model", "sonnet", "--settings", '{"model": "opus"}', "prompt"],
        ["--setting-sources", "user,project"],
        ["--version"],
        [],
    ],
)
def test_ordinary_prompts_and_option_values_pass(environment, hooks, args):
    cl.hook_coverage(environment["paths"], args, {"CLAUDE_CODE_SIMPLE": "0"})


def test_missing_or_edited_hooks_block_before_network(environment, monkeypatch):
    paths = environment["paths"]
    probe = Mock()
    monkeypatch.setattr(cl, "check", probe)
    monkeypatch.setattr(os, "execve", Mock())
    with pytest.raises(ValueError, match="hooks are missing"):
        cl.launch(paths, ["hello"])
    data = register_hooks(paths, model="mine")
    data["hooks"]["PreToolUse"][0]["hooks"][0]["onFailure"] = "ignore"
    paths.settings.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="project hook conflict"):
        cl.launch(paths, ["hello"])
    data["hooks"]["PreToolUse"][0]["hooks"][0]["onFailure"] = "block"
    paths.settings.write_text(json.dumps(dict(data, disableAllHooks=True)))
    with pytest.raises(ValueError, match="disable user hooks"):
        cl.launch(paths, ["hello"])
    probe.assert_not_called()


def test_project_settings_disabling_hooks_are_refused(environment, hooks, tmp_path, monkeypatch):
    project = tmp_path / "repo"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude/settings.local.json").write_text('{"disableAllHooks": true}')
    monkeypatch.chdir(project)
    with pytest.raises(ValueError, match="settings.local.json sets disableAllHooks"):
        cl.hook_coverage(environment["paths"], [], {})
    cl.hook_coverage(environment["paths"], ["--setting-sources", "user,project"], {})


def test_missing_binary(tmp_path):
    with pytest.raises(ValueError):
        cl.discover(tmp_path, path="/definitely/missing")


def test_interactive_progress_stays_in_terminal(monkeypatch):
    import io
    import sys
    import threading

    from claude_geo_guard import launch_progress

    cycled = threading.Event()

    class Terminal(io.StringIO):
        def isatty(self):
            return True

        def write(self, text):
            result = super().write(text)
            if text.startswith("\r") and text.endswith("   "):
                cycled.set()
            return result

    terminal = Terminal()
    monkeypatch.setattr(sys, "stderr", terminal)
    monkeypatch.setattr(launch_progress, "backend", Mock(side_effect=AssertionError))
    with launch_progress.terminal_progress():
        assert cycled.wait(timeout=5)
    frames = terminal.getvalue().rstrip("\n").split("\r")
    label = launch_progress.MESSAGE.removesuffix("…")
    assert frames[:5] == [label, label + ".  ", label + ".. ", label + "...", label + "   "]
    assert terminal.getvalue().endswith("\n")
