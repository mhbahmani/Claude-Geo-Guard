import shlex
import shutil

import pytest

from claude_geo_guard import shell


@pytest.mark.parametrize("name,rc", [("zsh", ".zshrc"), ("bash", ".bashrc")])
@pytest.mark.parametrize(
    "custom,expected",
    [
        ("", "Verified:"),
        ('claude() { command claude "$@"; }', "function handles claude first"),
        ("alias claude=/bin/false", "alias handles claude first"),
        ('export PATH="$HOME/other:$PATH"', "different executable"),
    ],
)
def test_routing_in_interactive_shell(isolated, monkeypatch, name, rc, custom, expected):
    executable = shutil.which(name)
    if not executable:
        pytest.skip(f"{name} unavailable")
    monkeypatch.setenv("SHELL", executable)
    wrapper = isolated.bin / "claude"
    other = isolated.home / "other" / "claude"
    for file in (wrapper, other):
        file.parent.mkdir(parents=True)
        file.write_text("#!/bin/sh\nexit 1\n")
        file.chmod(0o755)
    (isolated.home / rc).write_text(shell.block(isolated.bin) + custom + "\n")
    if name == "bash":
        (isolated.home / ".bash_profile").write_text('source "$HOME/.bashrc"\n')
    message = shell.routing_message(wrapper)
    assert expected in message
    assert shlex.quote(str(wrapper)) in message
