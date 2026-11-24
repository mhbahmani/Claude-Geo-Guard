import subprocess

UNIT = "claude-geo-guard.service"


def run(*args, check=True):
    return subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True, timeout=10, check=check
    )


def active():
    try:
        return run("is-active", UNIT, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def available():
    try:
        return run("show-environment", check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def unit(guard):
    # systemd specifiers and dollar expansion are separate from command quoting.
    value = (
        str(guard).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    )
    if "\n" in value or "\r" in value:
        raise ValueError("invalid service path")
    return f'''[Unit]
Description=Claude Geo Guard best-effort runtime monitor
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=simple
ExecStart="{value}" monitor
Restart=on-failure
RestartSec=5
TimeoutStopSec=10
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
'''


def best_effort(*args):
    """Service recovery must never prevent file rollback if the manager disappears."""
    try:
        return run(*args, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
