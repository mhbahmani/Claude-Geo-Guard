import os
import shlex
import subprocess
import sys
from pathlib import Path

START = "# >>> claude-geo-guard >>>"
END = "# <<< claude-geo-guard <<<"


def block(bin_dir):
    return f'\n{START}\nexport PATH={shlex.quote(str(bin_dir))}:"$PATH"\n{END}\n'


def add(text, managed):
    if START in text:
        if text.count(managed) != 1:
            raise ValueError("edited PATH block; reconcile shell config first")
        return text
    return text + managed


def remove(text, managed):
    if START in text and text.count(managed) != 1:
        raise ValueError("edited PATH block; preserving shell config")
    return text.replace(managed, "", 1)


def routing_message(wrapper):
    """Probe fresh interactive shell resolution without invoking the user's claude."""
    shell = os.environ.get("SHELL", "")
    name = Path(shell).name
    direct = shlex.quote(str(wrapper))
    if name == "zsh":
        lookup = "kind=$(builtin whence -w claude); kind=${kind#claude: }; file=$(builtin whence -p claude)"
        inspect = "whence -va claude"
    elif name == "bash":
        lookup = "kind=$(builtin type -t claude); file=$(builtin type -P claude)"
        inspect = "type -a claude"
    else:
        return f"WARNING: Shell routing unverified. Run 'type -a claude' in a new terminal or launch {direct} directly."

    probe = (
        lookup
        + """
case "$kind" in
    command|hashed|file)
        if [[ "$file" -ef "$1" ]]; then result=guarded; else result=bypass; fi ;;
    function|alias) result=$kind ;;
    *) result=unknown ;;
esac
builtin printf '\nGEO_GUARD_ROUTE=%s\n' "$result"
"""
    )
    try:
        result = subprocess.run(
            [
                shell,
                "-lic" if sys.platform == "darwin" else "-ic",
                probe,
                "geo-guard",
                str(wrapper),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=True,
        )
        markers = [
            line for line in result.stdout.splitlines() if line.startswith(b"GEO_GUARD_ROUTE=")
        ]
        route = markers[0].split(b"=", 1)[1] if len(markers) == 1 else b"unknown"
    except (OSError, subprocess.SubprocessError):
        route = b"unknown"
    if route == b"guarded":
        return f"Verified: a fresh interactive {name} resolves claude to {direct}."
    if route in (b"function", b"alias"):
        reason = f"A {name} {route.decode()} handles claude first. It may delegate to the guard or bypass it."
    elif route == b"bypass":
        reason = f"A fresh interactive {name} resolves claude to a different executable."
    else:
        reason = f"Could not verify how {name} resolves claude."
    return f"WARNING: {reason} Run '{inspect}' in a new terminal. To ensure guarded launches, invoke {direct} directly."
