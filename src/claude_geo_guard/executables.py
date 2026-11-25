"""Which executables may be monitored and terminated, and how updates are recognized."""

import json
import os
import re
import stat
from pathlib import Path

from .storage import read

# Shared runtimes that host many unrelated apps. Matching one would select every instance.
SHARED = {
    "brave",
    "brave-browser",
    "chrome",
    "chromium",
    "chromium-browser",
    "epiphany",
    "firefox",
    "firefoxpwa",
    "google-chrome",
    "librewolf",
    "microsoft-edge",
    "msedge",
    "opera",
    "vivaldi",
    "vivaldi-bin",
    "electron",
}
WEB_ARGUMENT = re.compile(
    r"^(https?://|--app(-id|-url)?(=|$)|--app-launch-url-for-shortcuts|--application-mode)", re.I
)


class Ambiguous(ValueError):
    """A Claude-looking launcher without evidence of a dedicated Claude installation."""


def dedicated(path, home=None):
    """A dedicated executable has a Claude-named file or directory below the home directory."""
    path = Path(path)
    home = Path(home or Path.home())
    parts = path.relative_to(home).parts if path.is_relative_to(home) else path.parts[1:]
    return path.name.lower() not in SHARED and any("claude" in part.lower() for part in parts)


def web_shortcut(args):
    return any(WEB_ARGUMENT.match(arg) for arg in args[1:])


def native(path):
    try:
        with Path(path).open("rb") as stream:
            return stream.read(4) == b"\x7fELF"
    except OSError:
        return False


def trusted_file(path):
    try:
        info = Path(path).stat()
    except OSError:
        return False
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid in {0, os.getuid()}
        and not info.st_mode & 0o022
        and os.access(path, os.X_OK)
    )


def accepted_update(launcher, validated):
    """The launcher's current target, if it is validated or a recognized in-place update.

    An update must stay in the same dedicated directory as a validated executable, as the
    native Claude Code updater does when it switches ``~/.local/bin/claude`` to a new
    ``versions/X.Y.Z`` file. Anything else needs a reinstall to be inspected.
    """
    try:
        current = Path(launcher).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if str(current) in validated:
        return str(current)
    if not (dedicated(current) and native(current) and trusted_file(current)):
        return None
    parents = {Path(known).parent for known in validated}
    return str(current) if current.parent in parents and dedicated(current.parent) else None


def resolve_launchers(launchers, validated):
    """Split stable launchers into accepted targets and unrecognized changes."""
    accepted, unrecognized = set(), {}
    for launcher in launchers:
        target = accepted_update(launcher, validated)
        if target is not None:
            accepted.add(target)
        else:
            try:
                unrecognized[launcher] = str(Path(launcher).resolve(strict=True))
            except (OSError, RuntimeError):
                unrecognized[launcher] = None
    return accepted, unrecognized


def load_manifest(paths):
    raw = read(paths.manifest)
    if raw is None:
        raise ValueError(f"{paths.manifest} is missing; rerun install")
    try:
        data = json.loads(raw)
    except ValueError as error:
        raise ValueError(f"{paths.manifest} is not valid JSON; rerun install") from error
    binaries = data.get("binaries") if isinstance(data, dict) else None
    if not isinstance(binaries, list) or not all(isinstance(b, str) for b in binaries):
        raise ValueError(f"{paths.manifest} has an unexpected structure; rerun install")
    return data


def manifest_launchers(manifest):
    """Stable launch paths; older manifests only recorded them in desktop edits."""
    if "launchers" in manifest:
        return list(manifest["launchers"])
    if not manifest.get("desktops"):
        return []
    from .platforms import backend

    desktop_entries = backend("desktop_entries")
    found = set()
    for desktop in manifest["desktops"]:
        for _section, before, _after in desktop["edits"]:
            if before.startswith("Exec="):
                try:
                    found.add(str(desktop_entries.launcher(before[5:])))
                except ValueError:
                    pass
    return sorted(found)


def validated(manifest):
    """Registered executables that still pass the dedicated-installation rule."""
    binaries = set(manifest.get("binaries", []))
    if manifest.get("mac_app"):
        return binaries
    return {binary for binary in binaries if dedicated(binary)}


def protected(manifest):
    """Executables to monitor, including accepted updates of stable launchers."""
    known = validated(manifest)
    accepted, _ = resolve_launchers(manifest_launchers(manifest), known)
    return known | accepted
