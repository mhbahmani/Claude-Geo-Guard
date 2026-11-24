import json
import os
import shutil
from pathlib import Path

from . import config
from .checker import check
from .timezone import launch_environment
from .platforms import backend


class LaunchBlocked(ValueError):
    """A launch failure with context for the desktop presentation."""

    def __init__(self, reason, *, kind, verdict=None):
        super().__init__(reason)
        self.kind = kind
        self.verdict = verdict


def launch(paths, args):
    systemd = backend("systemd")
    if not args:
        raise ValueError("missing desktop command")
    manifest = json.loads(paths.manifest.read_text())
    executable = shutil.which(args[0])
    if not executable or str(Path(executable).resolve()) not in manifest["binaries"]:
        raise LaunchBlocked(
            "desktop executable changed; rerun install to inspect the package update",
            kind="executable",
        )
    if not systemd.active():
        raise LaunchBlocked(
            "runtime monitor inactive; run systemctl --user restart claude-geo-guard",
            kind="monitor",
        )
    verdict = check()
    if not verdict.allowed:
        details = [verdict.reason]
        if verdict.allowed_country:
            details.append(f"Allowed country: {verdict.allowed_country}")
        for family, country in sorted(verdict.countries.items()):
            details.append(
                f"Detected IPv{family} country: {country}; IP: {verdict.ips.get(family, 'not returned')}"
            )
        details.append("Country provider: ifconfig.io")
        raise LaunchBlocked("\n".join(details), kind="verification", verdict=verdict)
    environment = launch_environment(paths)
    if config.load(paths)["success_notifications"]:
        from .platforms.linux.dialogs import show_success

        show_success(verdict)
    os.execve(executable, [executable, *args[1:]], environment)
