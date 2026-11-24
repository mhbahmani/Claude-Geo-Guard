import shutil
import subprocess
import sys
from pathlib import Path
from . import config, installer, cli_launcher, settings_manager
from .checker import check
from .storage import read, decode
from .platforms import backend


def diagnostics(paths):
    linux = sys.platform == "linux"
    if linux:
        desktop_entries, dialogs, systemd = (
            backend(n) for n in ("desktop_entries", "dialogs", "systemd")
        )
    verdict = check()
    manifest = installer.load_manifest(paths)
    report = {
        "country_verification": verdict.as_dict(),
        "environment": installer.inspect_environment(paths),
        "enforcement": "best-effort process control; NOT kernel network enforcement",
        "installed": bool(manifest),
        "checks_executed": ["live country verification"],
        "allowed_country": verdict.allowed_country,
        "functionally_verified": [f"current {verdict.allowed_country} egress at provider"]
        if verdict.allowed
        else [],
        "not_verified": [
            "real Claude hook execution, including Desktop bundled Code version",
            "GNOME/KDE menu, pinned launcher, D-Bus and URL activation",
            "remote/SSH/cloud sessions and managed settings overrides",
        ],
        "gaps": [
            installer.LIMITATION,
            "Per-destination VPN routing may differ from probe routing",
            "Applications may use private proxies; direct/alternative launchers bypass checks",
        ],
        "issues": [],
    }
    if not linux:
        report["not_verified"][1] = "Finder, Dock and real Claude Desktop GUI behavior"
    issues = report["issues"]
    report["config_file"] = str(paths.guard_config)
    report["timezone_scope"] = "new guarded Claude processes"
    try:
        report["configuration"] = config.load(paths)
        report["timezone"] = report["configuration"]["timezone"]
    except (ValueError, OSError) as error:
        report["timezone"] = None
        issues.append(f"Invalid guard configuration: {error}; edit {paths.guard_config}")
    for name in (
        ("curl", "ip", "systemctl", "desktop-file-validate")
        if linux
        else ("curl", "/sbin/route", "/sbin/ifconfig", "/usr/sbin/scutil")
    ):
        if not shutil.which(name):
            issues.append(
                f"Missing {name}; install curl ca-certificates iproute2 systemd desktop-file-utils"
            )
    try:
        binary = cli_launcher.discover(paths.bin, (manifest or {}).get("claude_binary"))
        report["claude_code_version"] = cli_launcher.version(binary)
        report["original_cli"] = str(binary)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        issues.append(str(error))
    report["path_wrapper_effective"] = shutil.which("claude") == str(paths.bin / "claude")
    if not report["path_wrapper_effective"]:
        issues.append(
            "PATH bypasses wrapper: open a new Bash/Zsh shell, then type -a claude; check aliases/functions"
        )
    report["monitor_active"] = linux and systemd.active()
    if not linux and (manifest or {}).get("mac_app"):
        report["guarded_application"] = str(backend("application").destination(paths))
        report["original_application"] = manifest["mac_app"]["path"]
        report["monitor_active"] = backend("service").healthy(paths)
        if not report["monitor_active"]:
            issues.append("macOS monitor is inactive")
    report["desktop_error_dialog"] = backend("dialogs").available()
    report["success_notifications_available"] = bool(shutil.which("notify-send"))
    try:
        entries = desktop_entries.discover(paths) if linux else []
        report["desktop_entries"] = [
            {"id": e.id, "path": str(e.source), "autostart": e.autostart} for e in entries
        ]
    except (ValueError, OSError) as error:
        issues.append(f"Desktop discovery: {error}")
    report["cli_wrapper_installed"] = bool(manifest) and (paths.bin / "claude").is_file()
    report["desktop_launcher_guards"] = []
    if manifest:
        if manifest["desktops"] and not report["desktop_error_dialog"]:
            issues.append(
                "Desktop error windows unavailable: install zenity (GNOME) or kdialog (KDE)"
            )
        for filename, record in manifest["files"].items():
            if record["kind"] == "file" and read(Path(filename)) != decode(record["after"]):
                issues.append(f"Managed file missing/changed: {filename}; inspect and reinstall")
        if manifest["desktops"] and not report["monitor_active"]:
            issues.append("Monitor inactive: systemctl --user restart claude-geo-guard.service")
        try:
            data = settings_manager.parse(read(paths.settings))
            expected = settings_manager.merge(
                data, manifest["groups"], paths.bin / "claude-geo-guard"
            )
            report["hooks_registered"] = data == expected
            if data != expected:
                issues.append("Required hooks missing: rerun install")
        except ValueError as error:
            report["hooks_registered"] = False
            issues.append(str(error))
        for entry in manifest["desktops"]:
            guard_status = {
                "id": entry["id"],
                "configuration": "installed",
                "functional": "not verified",
            }
            report["desktop_launcher_guards"].append(guard_status)
            raw = read(Path(entry["target"]))
            try:
                if raw is None:
                    raise ValueError("override missing")
                desktop_entries.restore(raw.decode(), entry["edits"])
                parsed = desktop_entries.values(raw.decode())
                if parsed["Desktop Entry"].get("DBusActivatable") == "true":
                    raise ValueError("D-Bus activation bypass enabled")
                source = Path(entry["source"])
                if (
                    source != Path(entry["target"])
                    and source.read_bytes().decode("utf-8") != entry["source_text"]
                ):
                    issues.append(f"Desktop package entry changed: {source}; rerun install")
                if shutil.which("desktop-file-validate"):
                    subprocess.run(
                        ["desktop-file-validate", entry["target"]],
                        capture_output=True,
                        timeout=5,
                        check=True,
                    )
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                guard_status["configuration"] = "conflict or missing"
                issues.append(f"Desktop override {entry['id']}: {error}; reconcile/reinstall")
    else:
        issues.append("Guard not installed; run ./install.sh")
    report["healthy_configuration"] = not issues
    return report
