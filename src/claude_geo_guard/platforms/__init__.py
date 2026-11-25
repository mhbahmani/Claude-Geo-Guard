import sys
from importlib import import_module


class Unroutable(RuntimeError):
    """Public destinations are deliberately unreachable, e.g. a VPN kill switch."""


def backend(name):
    systems = {"linux": "linux", "darwin": "macos"}
    if sys.platform not in systems:
        raise ValueError("supported platforms: Linux and macOS")
    if sys.platform == "darwin" and name not in {
        "environment",
        "networking",
        "dialogs",
        "processes",
        "service",
        "application",
    }:
        raise ValueError("macOS Desktop support is unavailable; use --skip-desktop")
    return import_module(f"{__name__}.{systems[sys.platform]}.{name}")


def snapshot():
    return backend("networking").snapshot()


def scan(*args):
    return backend("processes").scan(*args)


def terminate(*args, **kwargs):
    return backend("processes").terminate(*args, **kwargs)


def pause(processes):
    """Freeze processes where the platform supports it; returns those paused."""
    adapter = backend("processes")
    return adapter.pause(processes) if hasattr(adapter, "pause") else []


def resume(processes):
    adapter = backend("processes")
    if hasattr(adapter, "resume"):
        adapter.resume(processes)


def watch(callback):
    """Call back on kernel routing changes, or return None when unsupported."""
    adapter = backend("networking")
    return adapter.Watcher(callback) if hasattr(adapter, "Watcher") else None
