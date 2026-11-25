import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from claude_geo_guard.paths import Paths  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home with spaces"
    home.mkdir()
    for key, value in {
        "HOME": home,
        "XDG_DATA_HOME": home / ".local/share",
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_STATE_HOME": home / ".local/state",
        "XDG_RUNTIME_DIR": tmp_path / "run",
        "XDG_DATA_DIRS": tmp_path / "system/share",
        "XDG_CONFIG_DIRS": tmp_path / "system/xdg",
    }.items():
        monkeypatch.setenv(key, str(value))
    for key in list(os.environ):
        if "proxy" in key.lower() or key in {
            "ZDOTDIR",
            "CLAUDE_CONFIG_DIR",
            "DBUS_SESSION_BUS_ADDRESS",
            "DISPLAY",
            "WAYLAND_DISPLAY",
        }:
            monkeypatch.delenv(key, raising=False)
    return Paths.current()


def script(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/python3\n" + text)
    path.chmod(0o755)
    return path


@pytest.fixture
def environment(isolated, tmp_path, monkeypatch):
    bins = tmp_path / "tools"
    bins.mkdir()
    script(
        bins / "claude",
        """import json, os, sys
if sys.argv[1:] == ["--version"]:
    print("2.1.295 (Claude Code)")
else:
    print(json.dumps(sys.argv[1:]))
    sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
""",
    )
    script(
        bins / "curl",
        """import json, os, sys
family6 = "-6" in sys.argv
from pathlib import Path
def setting(name, default):
    file = Path(__file__).with_name(name)
    return file.read_text() if file.exists() else default
country = setting("FAKE_COUNTRY6" if family6 else "FAKE_COUNTRY", "GB")
ip = "2606:4700:4700::1111" if family6 else "1.1.1.1"
if sys.argv[-1].endswith("/cdn-cgi/trace"):
    from urllib.parse import urlsplit
    print("h="+urlsplit(sys.argv[-1]).hostname)
    print("loc="+country)
    print("ip="+ip)
else:
    sys.stdout.write(json.dumps({"country_code":country,"ip":ip}))
sys.stdout.write("\\n200")
sys.exit(int(setting("FAKE_CURL_EXIT","0")))
""",
    )
    script(
        bins / "ip",
        """import json, os, sys, time
if "monitor" in sys.argv:
    time.sleep(3600)
elif "get" in sys.argv:
    if "-6" in sys.argv and not os.environ.get("FAKE_IPV6"):
        print("RTNETLINK answers: Network is unreachable", file=sys.stderr)
        sys.exit(2)
    print(json.dumps([{"dev":"test0", "prefsrc":"192.0.2.1"}]))
else:
    print("[]")
""",
    )
    script(
        bins / "systemd-run",
        """import os, sys
command = sys.argv[sys.argv.index("--") + 1:]
os.execvp(command[0], command)
""",
    )
    # A started fake unit reports readiness for this live test process, like a real monitor.
    monkeypatch.setenv("FAKE_MONITOR_PID", str(os.getpid()))
    script(
        bins / "systemctl",
        """import json, os, sys
from pathlib import Path
state = Path(os.environ["HOME"]) / "systemd-state"
health = Path(os.environ["XDG_STATE_HOME"]) / "claude-geo-guard/monitor-health.json"
args = sys.argv[2:]
if args[0] in ("is-active", "is-enabled"):
    sys.exit(0 if state.exists() else 3)
if args[0] in ("start", "restart"):
    if os.environ.get("FAKE_SYSTEMD_FAIL"):
        sys.exit(1)
    state.touch()
    pid = int(os.environ["FAKE_MONITOR_PID"])
    start = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    health.parent.mkdir(parents=True, exist_ok=True)
    health.write_text(json.dumps({"pid": pid, "start": start, "status": "ready"}))
if args[0] in ("stop", "disable"):
    state.unlink(missing_ok=True)
    health.unlink(missing_ok=True)
""",
    )
    script(bins / "notify-send", "raise SystemExit(0)\n")
    script(bins / "zenity", "raise SystemExit(0)\n")
    # Unique native executable identity; /bin/sleep itself must never be a protected path.
    desktop = tmp_path / "Claude App" / "desktop"
    desktop.parent.mkdir()
    shutil.copy2("/bin/sleep", desktop)
    applications = Path(os.environ["XDG_DATA_DIRS"]) / "applications"
    applications.mkdir(parents=True)
    entry = applications / "com.anthropic.Claude.desktop"
    entry.write_text(f'''[Desktop Entry]
Type=Application
Name=Claude
Icon=claude
Categories=Development;
MimeType=x-scheme-handler/claude;
DBusActivatable=true
Exec="{desktop}" %U
Actions=New;

[Desktop Action New]
Name=New chat
Exec="{desktop}" %u
''')
    monkeypatch.setenv("PATH", f"{bins}:/usr/local/bin:/usr/bin:/bin:/usr/sbin")
    # Distribution detection is not an action on the host.
    monkeypatch.setattr(
        "platform.freedesktop_os_release", lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"}
    )
    return {"paths": isolated, "bins": bins, "desktop": desktop, "entry": entry}
