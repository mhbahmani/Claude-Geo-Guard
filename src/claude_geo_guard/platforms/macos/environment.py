import shutil


def preflight(*, skip_desktop, desktop_app):
    if not skip_desktop or desktop_app:
        raise ValueError(
            "macOS Desktop support awaits native validation; use --skip-desktop for CLI/hooks"
        )
    for command in ("curl", "/sbin/route", "/sbin/ifconfig", "/usr/sbin/scutil"):
        if not shutil.which(command):
            raise ValueError(f"missing {command}")
