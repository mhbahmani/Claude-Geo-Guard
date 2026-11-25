import os
import shutil
import sys
import time
from pathlib import Path

from . import config, executables, health
from .checker import check
from .launch_progress import verifying
from .timezone import launch_environment
from .platforms import backend

READY_TIMEOUT_SECONDS = 5


class LaunchBlocked(ValueError):
    """A launch failure with context for the desktop presentation."""

    def __init__(self, reason, *, kind, verdict=None):
        super().__init__(reason)
        self.kind = kind
        self.verdict = verdict


def resolve(manifest, program):
    """The executable to run, or a LaunchBlocked explaining why it is not recognized."""
    found = shutil.which(program)
    if not found:
        raise LaunchBlocked(f"desktop executable not found: {program}", kind="executable")
    resolved = str(Path(found).resolve())
    if resolved in set(manifest["binaries"]) - executables.validated(manifest):
        raise LaunchBlocked(
            f"{resolved} is shared with other applications and cannot be monitored safely; "
            "rerun install to review the desktop entries",
            kind="executable",
        )
    if resolved not in executables.protected(manifest):
        raise LaunchBlocked(
            f"desktop executable changed to {resolved}; rerun install to inspect the update",
            kind="executable",
        )
    return found


def wait_until_ready(paths, systemd, timeout=READY_TIMEOUT_SECONDS):
    if not systemd.active():
        # A unit that hit its start limit refuses to start until the failure is cleared.
        systemd.best_effort("reset-failed", systemd.UNIT)
        if not (systemd.best_effort("start", systemd.UNIT) and systemd.active()):
            raise LaunchBlocked(
                "runtime monitor inactive; run systemctl --user restart claude-geo-guard",
                kind="monitor",
            )
    deadline = time.monotonic() + timeout
    while True:
        status, reason = health.current(paths)
        if status == "ready":
            return
        if status == "degraded" or time.monotonic() >= deadline:
            raise LaunchBlocked(
                f"runtime monitor is not ready: {reason}. Run claude-geo-guard doctor",
                kind="monitor",
            )
        time.sleep(0.1)


def launch(paths, args):
    if sys.platform == "darwin":
        return backend("application").launch(paths, args)
    systemd = backend("systemd")
    if not args:
        raise ValueError("missing desktop command")
    executable = resolve(executables.load_manifest(paths), args[0])
    preferences = config.load(paths)
    if not preferences["enabled"]:
        print(config.DISABLED_MESSAGE, file=sys.stderr, flush=True)
        backend("dialogs").show_warning(config.DISABLED_MESSAGE)
        return os.execve(executable, [executable, *args[1:]], os.environ)
    with verifying():
        wait_until_ready(paths, systemd)
        verdict = check()
        if not verdict.allowed:
            details = [verdict.reason]
            if verdict.allowed_country:
                details.append(f"Allowed country: {verdict.allowed_country}")
            for family, country in sorted(verdict.countries.items()):
                details.append(
                    f"Detected IPv{family} country: {country}; IP: {verdict.ips.get(family, 'not returned')}"
                )
            details.append("Country provider: Claude edge traces (plus ifconfig.io in full mode)")
            raise LaunchBlocked("\n".join(details), kind="verification", verdict=verdict)
        environment = launch_environment(paths)
    if preferences["success_notifications"]:
        from .platforms.linux.dialogs import show_success

        show_success(verdict)
    os.execve(executable, [executable, *args[1:]], environment)
