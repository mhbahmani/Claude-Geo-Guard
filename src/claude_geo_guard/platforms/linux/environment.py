import os
import platform
import shutil


def preflight(*, skip_desktop, desktop_app):
    if desktop_app:
        raise ValueError("--desktop-app requires macOS Desktop support")
    distro = platform.freedesktop_os_release()
    minimum = {"ubuntu": 22, "debian": 12}
    if (
        distro.get("ID") not in minimum
        or int(distro.get("VERSION_ID", "0").split(".")[0]) < minimum[distro["ID"]]
    ):
        raise ValueError("supported distributions: Ubuntu 22.04+ and Debian 12+")
    if not hasattr(os, "pidfd_open"):
        raise ValueError("Python pidfd support is required")
    for name in ("curl", "ip"):
        if not shutil.which(name):
            raise ValueError(f"missing {name}; install curl, ca-certificates and iproute2")
