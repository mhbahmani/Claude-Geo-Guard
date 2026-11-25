"""Dedicated systemd user scopes: every descendant of a guarded launch, even a detached one."""

import shutil
import time
import uuid
from pathlib import Path

PREFIX = "claude-geo-guard-desktop-"
CGROUPS = Path("/sys/fs/cgroup")


def command(argv):
    """Prefix a launch so it runs in its own scope; the PID and environment stay the same."""
    runner = shutil.which("systemd-run")
    if runner is None:
        return argv
    unit = f"--unit={PREFIX}{uuid.uuid4().hex[:12]}"
    return [runner, "--user", "--scope", "--quiet", "--collect", unit, "--", *argv]


def scope(pid, proc=Path("/proc")):
    """The guard scope a process runs in, as a cgroup path, or None."""
    try:
        text = (proc / str(pid) / "cgroup").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("0::"):
            path = line[3:].strip()
            name = path.rsplit("/", 1)[-1]
            return path if name.startswith(PREFIX) and name.endswith(".scope") else None
    return None


def _directory(path):
    return CGROUPS / path.lstrip("/")


def members(path):
    try:
        return [int(pid) for pid in (_directory(path) / "cgroup.procs").read_text().split()]
    except (OSError, ValueError):
        return []


def _settle(path, field, value, timeout):
    deadline = time.monotonic() + timeout
    while True:
        try:
            text = (_directory(path) / "cgroup.events").read_text()
        except OSError:
            return False
        if dict(line.split(" ", 1) for line in text.splitlines()).get(field) == value:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


def freeze(path, frozen=True, timeout=1.0):
    """Freeze or thaw a whole scope atomically, including processes created meanwhile."""
    try:
        (_directory(path) / "cgroup.freeze").write_text("1" if frozen else "0")
    except OSError:
        return False
    return _settle(path, "frozen", "1" if frozen else "0", timeout)


def kill(path, timeout=2.0):
    """SIGKILL every member at once; returns the PIDs still present after the timeout."""
    try:
        (_directory(path) / "cgroup.kill").write_text("1")
    except OSError:
        return members(path)
    deadline = time.monotonic() + timeout
    while (remaining := members(path)) and time.monotonic() < deadline:
        time.sleep(0.02)
    return remaining
