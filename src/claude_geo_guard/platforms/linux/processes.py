"""Same-UID executable evidence + start times + pidfds; never name-based kills."""

import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Process:
    pid: int
    start: int
    parent: int
    executable: str
    tty: int = field(default=0, compare=False)


def inspect(pid, proc=Path("/proc")):
    try:
        directory = proc / str(pid)
        if directory.stat().st_uid != os.getuid():
            return None
        fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
        exe = os.readlink(directory / "exe").removesuffix(" (deleted)")
        return Process(pid, int(fields[19]), int(fields[1]), exe, int(fields[4]))
    except (OSError, ValueError, IndexError):
        return None


def scan(binaries, previous=(), proc=Path("/proc")):
    if not previous:
        # While idle, only look for a registered executable. Read ownership,
        # start times and ancestry in the full scan once a candidate appears.
        for directory in proc.iterdir():
            if directory.name.isdigit():
                try:
                    if os.readlink(directory / "exe").removesuffix(" (deleted)") in binaries:
                        break
                except OSError:
                    continue
        else:
            return []
    all_processes = {
        int(p.name): inspect(int(p.name), proc) for p in proc.iterdir() if p.name.isdigit()
    }
    all_processes = {pid: p for pid, p in all_processes.items() if p is not None}
    identities = {(p.pid, p.start) for p in previous}
    protected = {
        pid: p
        for pid, p in all_processes.items()
        if p.executable in binaries or (p.pid, p.start) in identities
    }
    while True:
        children = {
            pid: p
            for pid, p in all_processes.items()
            if p.parent in protected
            and pid not in protected
            and p.start >= protected[p.parent].start
        }
        if not children:
            break
        protected.update(children)
    protected.pop(os.getpid(), None)
    return list(protected.values())


def send(process, sig):
    # Open first, then revalidate identity. pidfd never targets a subsequently reused PID.
    fd = None
    try:
        fd = os.pidfd_open(process.pid)
        current = inspect(process.pid)
        if current and (current.pid, current.start) == (process.pid, process.start):
            signal.pidfd_send_signal(fd, sig)
            return True
    except ProcessLookupError:
        pass
    finally:
        if fd is not None:
            os.close(fd)
    return False


def pause(processes):
    # Stopping a terminal's foreground job hands the terminal back to the shell; those
    # sessions keep relying on their hooks instead.
    return [p for p in processes if p.tty == 0 and send(p, signal.SIGSTOP)]


def resume(processes):
    for process in processes:
        send(process, signal.SIGCONT)


def terminate(processes, grace=5, stop=None):
    for process in processes:
        send(process, signal.SIGTERM)
        # A paused process handles the pending SIGTERM only once it runs again.
        send(process, signal.SIGCONT)
    deadline = time.monotonic() + grace
    remaining = list(processes)
    while remaining and time.monotonic() < deadline:
        remaining = [p for p in remaining if (now := inspect(p.pid)) and now.start == p.start]
        if stop is not None and stop.wait(0.1):
            break
        if stop is None:
            time.sleep(0.1)
    for process in remaining:
        send(process, signal.SIGKILL)
