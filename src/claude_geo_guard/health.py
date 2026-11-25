"""Monitor readiness shared by the service, the Desktop launcher and doctor."""

import json
import os

from .platforms import backend
from .storage import atomic_write, json_bytes, read


def _identity(pid):
    try:
        process = backend("processes").inspect(pid)
    except (OSError, ValueError):
        return None
    # JSON turns tuples into lists; compare in the stored form.
    return None if process is None else json.loads(json.dumps(process.start))


def publish(paths, status, reason=None):
    record = {"pid": os.getpid(), "start": _identity(os.getpid()), "status": status}
    if reason:
        record["reason"] = reason
    atomic_write(paths.monitor_health, json_bytes(record))


def withdraw(paths):
    try:
        data = json.loads(read(paths.monitor_health) or b"{}")
        if data.get("pid") == os.getpid():
            paths.monitor_health.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def current(paths):
    """Return (status, reason). Status is ready, degraded or stopped."""
    try:
        raw = read(paths.monitor_health)
        data = json.loads(raw) if raw is not None else None
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("pid"), int):
        return "stopped", "the runtime monitor has not reported that it is ready"
    if data.get("start") is None or _identity(data["pid"]) != data["start"]:
        return "stopped", "the runtime monitor that reported readiness is no longer running"
    if data.get("status") == "ready":
        return "ready", None
    return "degraded", str(data.get("reason") or "the runtime monitor reported a problem")
