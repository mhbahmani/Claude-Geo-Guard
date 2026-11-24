import sys
from importlib import import_module


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
