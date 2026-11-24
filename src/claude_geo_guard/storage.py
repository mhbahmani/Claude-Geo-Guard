"""Atomic writes, advisory serialization and conflict-aware rollback."""

import base64
import fcntl
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path


def read(path):
    if path.is_symlink():
        raise ValueError(f"refusing symlink: {path}")
    return path.read_bytes() if path.exists() else None


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    read(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".guard-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        dirfd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def encode(data):
    return None if data is None else base64.b64encode(data).decode("ascii")


def decode(data):
    return None if data is None else base64.b64decode(data)


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


@contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


class Transaction:
    def __init__(self, backup):
        self.backup = backup
        self.entries = []

    def write(self, path, data, mode=0o600):
        before = read(path)
        old_mode = stat.S_IMODE(path.stat().st_mode) if before is not None else mode
        entry = {
            "path": str(path),
            "before": encode(before),
            "after": encode(data),
            "mode": old_mode,
        }
        self.entries.append(entry)
        # Durable recovery journal is written before each mutation.
        atomic_write(self.backup, json_bytes(self.entries))
        if data is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, data, mode)

    def rollback(self):
        conflicts = []
        for entry in reversed(self.entries):
            path = Path(entry["path"])
            if read(path) != decode(entry["after"]):
                conflicts.append(str(path))
                continue
            before = decode(entry["before"])
            if before is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, before, entry["mode"])
        return conflicts
