import fcntl
import os
import plistlib
import subprocess
import time

LABEL = "local.claude-geo-guard.monitor"


def plist(paths):
    return paths.home / "Library/LaunchAgents" / (LABEL + ".plist")


def contents(paths):
    return plistlib.dumps(
        dict(
            Label=LABEL,
            ProgramArguments=[str(paths.bin / "claude-geo-guard"), "monitor"],
            RunAtLoad=True,
            KeepAlive=True,
            ThrottleInterval=10,
            Umask=0o077,
        )
    )


def domain():
    return f"gui/{os.getuid()}"


def run(*args, check=True):
    return subprocess.run(
        ["/bin/launchctl", *args], check=check, text=True, capture_output=True, timeout=10
    )


def status(paths):
    result = run("print", domain() + "/" + LABEL, check=False)
    if result.returncode:
        if "Could not find service" in result.stderr:
            return ""
        raise ValueError("launchd GUI session unavailable: " + result.stderr.strip())
    if not any(line.strip() == f"path = {plist(paths)}" for line in result.stdout.splitlines()):
        raise ValueError("existing LaunchAgent has a conflicting source path")
    return result.stdout


def active(paths):
    return any(line.strip() == "state = running" for line in status(paths).splitlines())


def healthy(paths):
    if not active(paths):
        return False
    try:
        with (paths.manifest.parent / "monitor.lock").open("rb") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
    except FileNotFoundError:
        pass
    return False


def stop(paths):
    if status(paths):
        run("bootout", domain() + "/" + LABEL)


def start(paths):
    run("bootstrap", domain(), str(plist(paths)))
    for _ in range(30):
        if healthy(paths):
            return
        time.sleep(0.1)
    raise ValueError("LaunchAgent failed to start")
