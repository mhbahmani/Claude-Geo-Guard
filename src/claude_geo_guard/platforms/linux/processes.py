"""Same-UID executable evidence + start times + pidfds; never name-based kills."""

import ipaddress
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import containment


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


def expand(processes, proc=Path("/proc")):
    """Add every member of the guard scopes these processes run in, including helpers that
    detached from the process tree."""
    found = {(p.pid, p.start): p for p in processes}
    for path in {s for p in processes if (s := containment.scope(p.pid, proc))}:
        for pid in containment.members(path):
            if (member := inspect(pid, proc)) is not None:
                found.setdefault((member.pid, member.start), member)
    return [p for p in found.values() if p.pid != os.getpid()]


def _arguments(pid, proc):
    try:
        return (proc / str(pid) / "cmdline").read_bytes().decode(errors="replace").split("\0")
    except OSError:
        return []


def interface(process, binaries, proc=Path("/proc")):
    """Draws or serves the window. Chromium renderers and GPU processes cannot open
    sockets; web requests go through the network service, which is not part of this."""
    if process.executable not in binaries:
        return False
    options = dict(
        arg[2:].split("=", 1)
        for arg in _arguments(process.pid, proc)
        if arg.startswith("--") and "=" in arg
    )
    kind = options.get("type")
    if kind == "utility":
        return options.get("utility-sub-type") != "network.mojom.NetworkService"
    return kind in {None, "renderer", "gpu-process", "zygote"}


def _public(address):
    host, _, port = address.rpartition(":")
    if int(port, 16) == 0:
        return False
    raw = bytes.fromhex(host)
    # /proc/net stores each 32-bit word in host byte order.
    ip = ipaddress.ip_address(b"".join(raw[i : i + 4][::-1] for i in range(0, len(raw), 4)))
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local)


def connected(processes, proc=Path("/proc")):
    """Processes holding a connection to a non-local address: TCP established or
    connecting, or a connected UDP socket."""
    owners = {}
    for process in processes:
        try:
            for entry in os.scandir(proc / str(process.pid) / "fd"):
                target = os.readlink(entry.path)
                if target.startswith("socket:["):
                    owners[target[8:-1]] = process
        except OSError:
            continue
    found = []
    if not owners:
        return found
    for table in ("tcp", "tcp6", "udp", "udp6"):
        try:
            rows = (proc / "net" / table).read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            owner = owners.get(fields[9]) if len(fields) > 9 else None
            if owner is None or owner in found:
                continue
            if table.startswith("tcp") and fields[3] not in {"01", "02"}:
                continue
            if _public(fields[2]):
                found.append(owner)
    return found


def pause(processes, binaries=frozenset()):
    """Freeze processes that can reach the network and keep the window responsive.
    Returns the processes stopped."""
    # Stopping a terminal's foreground job hands the terminal back to the shell; those
    # sessions keep relying on their hooks instead.
    return [
        p
        for p in expand(processes)
        if p.tty == 0 and not interface(p, binaries) and send(p, signal.SIGSTOP)
    ]


def resume(processes):
    for process in processes:
        send(process, signal.SIGCONT)


def _alive(process):
    current = inspect(process.pid)
    return current is not None and current.start == process.start


def halt(processes, timeout=2.0):
    """Stop everything before killing it, so nothing more is sent and no child is spawned
    in response to SIGTERM. Returns the processes still alive after the timeout."""
    scopes = {s for p in processes if (s := containment.scope(p.pid))}
    for path in scopes:
        containment.freeze(path)
    stopped = {}
    pending = expand(processes)
    # Outside a scope, stop until no new descendant appears; stopped processes cannot fork.
    for _ in range(20):
        fresh = [p for p in pending if (p.pid, p.start) not in stopped]
        if not fresh:
            break
        for process in fresh:
            send(process, signal.SIGSTOP)
            stopped[process.pid, process.start] = process
        pending = scan(frozenset(), list(stopped.values()))
    for process in stopped.values():
        send(process, signal.SIGKILL)
    remaining = {pid for path in scopes for pid in containment.kill(path, timeout)}
    deadline = time.monotonic() + timeout
    while (survivors := [p for p in stopped.values() if _alive(p)]) and time.monotonic() < deadline:
        time.sleep(0.02)
    known = {p.pid for p in survivors}
    survivors += [p for pid in remaining - known if (p := inspect(pid)) is not None]
    return survivors


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
