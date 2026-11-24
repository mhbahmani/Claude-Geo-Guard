"""Inspect same-user processes with libproc and revalidate immediately before kill."""

import ctypes
import os
import signal
import struct
import time
from dataclasses import dataclass
from functools import lru_cache

# proc_bsdinfo from Darwin sys/proc_info.h, PROC_PIDTBSDINFO.
BSD = struct.Struct("=12I48s6I2Q")


@lru_cache
def lib():
    library = ctypes.CDLL("/usr/lib/libproc.dylib")
    library.proc_pidinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    library.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    library.proc_listpids.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    return library


@dataclass(frozen=True)
class Process:
    pid: int
    start: tuple
    parent: int
    executable: str


def inspect(pid):
    data = ctypes.create_string_buffer(BSD.size)
    if lib().proc_pidinfo(pid, 3, 0, data, BSD.size) != BSD.size:
        return None
    fields = BSD.unpack(data.raw)
    if fields[3] != pid or fields[5] != os.getuid() or fields[7] != os.getuid():
        return None
    path = ctypes.create_string_buffer(4096)
    if lib().proc_pidpath(pid, path, len(path)) <= 0:
        return None
    return Process(pid, fields[-2:], fields[4], os.fsdecode(path.value))


def scan(binaries, previous=()):
    capacity = 256
    while True:
        pids = (ctypes.c_int * capacity)()
        size = lib().proc_listpids(3, os.getuid(), pids, ctypes.sizeof(pids))
        if size <= 0:
            raise OSError("could not enumerate user processes")
        if size < ctypes.sizeof(pids):
            break
        capacity *= 2
    processes = [
        p for pid in pids[: size // 4] if pid > 1 and pid != os.getpid() and (p := inspect(pid))
    ]
    known = {(p.pid, p.start, p.executable) for p in previous}
    selected = {
        p.pid: p
        for p in processes
        if p.executable in binaries or (p.pid, p.start, p.executable) in known
    }
    while True:
        children = {
            p.pid: p
            for p in processes
            if p.pid not in selected
            and p.parent in selected
            and p.start >= selected[p.parent].start
        }
        if not children:
            return list(selected.values())
        selected.update(children)


def send(p, sig):
    current = inspect(p.pid)
    if current and (current.start, current.executable) == (p.start, p.executable):
        try:
            os.kill(p.pid, sig)
        except ProcessLookupError:
            pass


def terminate(processes, grace=5, stop=None):
    for p in processes:
        send(p, signal.SIGTERM)
    if stop:
        stop.wait(grace)
    else:
        time.sleep(grace)
    for p in processes:
        send(p, signal.SIGKILL)
