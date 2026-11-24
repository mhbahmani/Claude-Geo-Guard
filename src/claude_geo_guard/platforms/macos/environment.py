import shutil


def preflight(*, skip_desktop, desktop_app):
    if skip_desktop and desktop_app:
        raise ValueError("--desktop-app conflicts with --skip-desktop")
    commands = ["curl", "/sbin/route", "/sbin/ifconfig", "/usr/sbin/scutil"]
    if not skip_desktop:
        commands += [
            "/usr/bin/osacompile",
            "/usr/bin/osascript",
            "/usr/bin/codesign",
            "/bin/launchctl",
            "/usr/bin/open",
        ]
    for command in commands:
        if not shutil.which(command):
            raise ValueError(f"missing {command}")
