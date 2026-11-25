"""User-local, journaled installation. No firewall or vendor executable changes."""

import json
import os
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import cli_launcher, config, settings_manager, verdict_cache
from . import shell
from .executables import Ambiguous
from .platforms import backend
from .policy import normalize_country
from .storage import Transaction, decode, encode, json_bytes, lock, read
from .timezone import normalize_timezone

LIMITATION = (
    "BEST-EFFORT: launch checks and periodic termination are not network enforcement. "
    "Traffic can occur before detection; direct executables and configuration changes bypass protection."
)


def load_manifest(paths):
    raw = read(paths.manifest)
    try:
        return json.loads(raw) if raw else None
    except ValueError as error:
        raise ValueError(
            f"installation manifest {paths.manifest} is not valid JSON ({error}); restore it "
            f"from {paths.manifest.parent / 'backups'} before changing the installation"
        ) from None


def inspect_environment(paths):
    if sys.platform == "darwin":
        return {
            "os": "Darwin",
            "distribution": platform.mac_ver()[0],
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "shell": os.environ.get("SHELL", "unknown"),
            "home": str(paths.home),
            "desktop_support": "Claude Guard.app and user LaunchAgent (experimental)",
        }
    distro = platform.freedesktop_os_release()
    return {
        "os": platform.system(),
        "distribution": distro.get("PRETTY_NAME", "unknown"),
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP", "unknown"),
        "graphical_session": bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        "shell": os.environ.get("SHELL", "unknown"),
        "python": platform.python_version(),
        "systemd_user": backend("systemd").available(),
        "home": str(paths.home),
    }


def preflight(
    paths,
    *,
    claude_binary=None,
    skip_desktop=False,
    interval=None,
    grace=None,
    country=None,
    split_tunnel=None,
    allowed_ips=None,
    success_notifications=None,
    timezone=None,
    desktop_app=None,
    enabled=None,
):
    backend("environment").preflight(skip_desktop=skip_desktop, desktop_app=desktop_app)
    linux_desktop = sys.platform == "linux" and not skip_desktop
    desktop_entries = backend("desktop_entries") if linux_desktop else None
    systemd = backend("systemd") if linux_desktop else None
    if not Path(os.environ.get("ZDOTDIR") or paths.home).is_absolute():
        raise ValueError("ZDOTDIR must be absolute for reversible shell integration")
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        raise ValueError("custom CLAUDE_CONFIG_DIR unsupported; use standard ~/.claude settings")
    old = load_manifest(paths)
    config_raw = read(paths.guard_config)
    # Missing installed configuration is never silently replaced with defaults.
    if old and config_raw is None and (country is None or timezone is None):
        raise ValueError(
            f"missing {paths.guard_config}; restore it or provide "
            "--country CODE and --timezone ZONE (or inherit)"
        )
    current_config = config.parse(config_raw) if config_raw is not None else dict(config.DEFAULTS)
    chosen = dict(current_config)
    for key, value in (
        ("enabled", enabled),
        ("allowed_country", country),
        ("split_tunnel", split_tunnel),
        ("allowed_ips", allowed_ips),
        ("success_notifications", success_notifications),
        ("timezone", timezone),
        ("interval", interval),
        ("grace", grace),
    ):
        if value is not None:
            chosen[key] = value
    chosen = config.validate(chosen)
    country, timezone, interval, grace = (
        chosen[k] for k in ("allowed_country", "timezone", "interval", "grace")
    )
    config_data = (
        config_raw if config_raw is not None and chosen == current_config else json_bytes(chosen)
    )
    binary = cli_launcher.discover(paths.bin, claude_binary or (old or {}).get("claude_binary"))
    code_version = cli_launcher.version(binary)
    guard = paths.bin / "claude-geo-guard"
    original_raw = read(paths.settings)
    settings = settings_manager.parse(original_raw)
    groups = settings_manager.owned_groups(guard)
    if not old and any(
        settings_manager.mentions(group, guard)
        for groups_ in settings.get("hooks", {}).values()
        for group in groups_
    ):
        raise ValueError(
            "guard hooks exist without an ownership manifest; reconcile before installing"
        )
    merged = settings_manager.merge(settings, groups, guard)
    if old and skip_desktop and (old["desktops"] or old.get("mac_app")):
        raise ValueError("uninstall first before changing to CLI-only mode")
    mac_app = None
    if sys.platform == "darwin" and not skip_desktop:
        application = backend("application")
        mac_app = application.discover(
            paths, desktop_app or ((old or {}).get("mac_app") or {}).get("path")
        )
        if not (old or {}).get("mac_app") and application.destination(paths).exists():
            raise ValueError("refusing to overwrite existing Claude Guard.app")
        backend("service").run("print", backend("service").domain())
    entries = desktop_entries.discover(paths) if linux_desktop else []
    desktops, skipped, released, launchers = [], [], [], set()
    binaries = {mac_app["executable"]} if mac_app else set()
    old_desktops = {d["target"]: d for d in (old or {}).get("desktops", [])}
    # Keep tracking hidden/removed entries to avoid orphaning an override on repeat install.
    found = {str(e.target) for e in entries}
    for target, record in old_desktops.items():
        if target not in found and Path(target).is_file():
            entries.append(
                desktop_entries.Entry(
                    record["id"],
                    Path(record["source"]),
                    Path(target),
                    Path(target).read_bytes().decode("utf-8"),
                    record["autostart"],
                )
            )
    for entry in entries:
        previous = old_desktops.get(str(entry.target))
        current_text = entry.text
        source_text = entry.text
        restore_text = entry.text if entry.source == entry.target else None
        if previous:
            # A launcher may have been regenerated verbatim without our wrapper.
            # Only trust an exact saved original; unfamiliar launch commands stay conflicts.
            if entry.text == previous["restore_text"]:
                restored = entry.text
            else:
                try:
                    restored = desktop_entries.restore(entry.text, previous["edits"])
                except ValueError as error:
                    raise ValueError(f"{entry.target}: {error}") from error
            source = Path(previous["source"])
            if source != entry.target:
                latest = source.read_bytes().decode("utf-8")
                customized = previous["restore_text"] is not None or entry.text != previous["after"]
                if customized:
                    if latest != previous["source_text"]:
                        raise ValueError(
                            f"both package and override edited: {entry.target}; reconcile first"
                        )
                    restore_text = restored
                else:
                    restored = latest
                    restore_text = None
                source_text = latest
            else:
                restore_text = restored
                source_text = restored
            entry.source = source
            entry.text = restored
        try:
            changed, exes, edits = desktop_entries.intercept(entry, guard)
        except Ambiguous as error:
            skipped.append(str(error))
            if previous:
                # An older release registered this shortcut; hand it back unguarded.
                released.append(
                    {
                        "target": str(entry.target),
                        "current": current_text,
                        "data": restore_text,
                    }
                )
            continue
        binaries.update(exes)
        parsed = desktop_entries.values(entry.text)
        launchers.update(
            str(desktop_entries.launcher(section["Exec"]))
            for name, section in parsed.items()
            if "Exec" in section and (name == "Desktop Entry" or name.startswith("Desktop Action "))
        )
        desktops.append(
            {
                "id": entry.id,
                "source": str(entry.source),
                "target": str(entry.target),
                "source_text": source_text,
                "restore_text": restore_text,
                "after": changed,
                "edits": edits,
                "autostart": entry.autostart,
            }
        )
    if desktops and not systemd.available():
        raise ValueError("systemd user manager unavailable; log into a desktop session and retry")
    return {
        "allowed_country": country,
        "timezone": timezone,
        "enabled": chosen["enabled"],
        "config_raw": config_raw,
        "config_data": config_data,
        "config_path": str(paths.guard_config),
        "split_tunnel": chosen["split_tunnel"],
        "allowed_ips": chosen["allowed_ips"],
        "success_notifications": chosen["success_notifications"],
        "environment": inspect_environment(paths),
        "old": old,
        "binary": str(binary),
        "version": code_version,
        "groups": groups,
        "settings": merged,
        "settings_raw": original_raw,
        "desktops": desktops,
        "skipped_desktops": skipped,
        "released_desktops": released,
        "launchers": sorted(launchers),
        "mac_app": mac_app,
        "binaries": sorted(binaries),
        "interval": interval,
        "grace": grace,
        "claude_binary": claude_binary or (old or {}).get("claude_binary"),
    }


def summary(plan):
    dialogs = backend("dialogs")
    return {
        "allowed_country": plan["allowed_country"],
        "timezone": plan["timezone"],
        "enabled": plan["enabled"],
        "config_file": plan["config_path"],
        "split_tunnel": plan["split_tunnel"],
        "allowed_ips": plan["allowed_ips"],
        "success_notifications": plan["success_notifications"],
        "grace": plan["grace"],
        "timezone_applies_to": "new guarded Claude processes; fully quit existing Desktop to apply",
        "environment": plan["environment"],
        "claude_code": plan["binary"],
        "version": plan["version"],
        "desktop_overrides": [d["target"] for d in plan["desktops"]],
        "unprotected_shortcuts": plan.get("skipped_desktops", []),
        "monitor": bool(plan["desktops"] or plan.get("mac_app")),
        "mac_app": plan.get("mac_app"),
        "desktop_error_dialog": dialogs.available(),
        "platform": sys.platform,
        "desktop_support": "Linux desktop entries or experimental macOS launcher",
        "interval": plan["interval"],
        "changes": [
            "private Python package and two launchers",
            "three appended user hook groups",
            "marked PATH blocks in .bashrc, .profile and Zsh .zshrc/.zprofile",
        ],
        "limitations": [
            LIMITATION,
            "Use Claude Guard.app for launch checks. Direct opens are checked periodically."
            if plan.get("mac_app")
            else "CLI and hooks only.",
        ]
        if sys.platform == "darwin"
        else [
            LIMITATION,
            "Desktop bundled Code version and real hook execution NOT VERIFIED",
            "GNOME/KDE menu, favorites and URL activation NOT VERIFIED",
            "Restart existing Claude sessions after installation",
            "Enabling the monitor may immediately close existing Desktop processes on verification failure",
            "Desktop denial windows require zenity (GNOME) or kdialog (KDE); "
            "notifications/stderr are the fallback if a window cannot be shown",
            "No Desktop entries found: Desktop is unprotected"
            if not plan["desktops"]
            else "Only listed desktop entries are intercepted",
        ],
    }


def hook_fast_path(paths):
    """Shell lines that allow a hook from a fresh shared verdict without starting Python.

    Anything else (a denial, a stale or missing verdict, a proxy environment,
    Cursor's envelope) falls through to the full hook."""
    verdict, settings = shlex.quote(str(paths.verdict)), shlex.quote(str(paths.guard_config))
    proxies = "".join("${" + name + "-}" for name in verdict_cache.PROXIES)
    return (
        f'if [ "${{1-}}" = hook ] && [ -z "${{CURSOR_VERSION-}}{proxies}" ] \\\n'
        f"  && [ -f {settings} ] && [ -f /etc/hosts ] && [ {verdict} -nt {settings} ] && [ {verdict} -nt /etc/hosts ] \\\n"
        f"  && read -r expires state rest 2>/dev/null <{verdict} \\\n"
        '  && [ "$expires" -gt "$(date +%s)" ] 2>/dev/null; then\n'
        '  case "$state" in allow | unverified) exit 0 ;; esac\n'
        "fi\n"
    )


def journal_path(paths):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return paths.manifest.parent / "backups" / f"{stamp}-{uuid.uuid4().hex[:8]}.json"


def install(paths, plan):
    systemd = backend("systemd") if plan["desktops"] else None
    with lock(paths.manifest.parent / "install.lock"):
        # Refuse racing editors between planning and mutation.
        if (
            read(paths.settings) != plan["settings_raw"]
            or load_manifest(paths) != plan["old"]
            or read(paths.guard_config) != plan["config_raw"]
        ):
            raise ValueError("configuration changed since inspection; retry")
        old = plan["old"] or {}
        tx = Transaction(journal_path(paths))
        records = dict(old.get("files", {}))
        replaced = []
        guard = paths.bin / "claude-geo-guard"
        was_active = systemd.active() if plan["desktops"] else False
        was_enabled = (
            systemd.run("is-enabled", systemd.UNIT, check=False).returncode == 0
            if plan["desktops"]
            else False
        )

        def write(path, data, mode=0o600, kind="file"):
            key = str(path)
            before = read(path)
            prior = records.get(key)
            if kind == "package":
                # The private package only ever holds guard code; a stale or hand-copied
                # module must not block the update. The journal keeps the old contents.
                if before is not None and (not prior or before != decode(prior["after"])):
                    if before != data:
                        replaced.append(key)
                    prior = {
                        "before": prior["before"] if prior else encode(None),
                        "after": encode(before),
                        "mode": prior["mode"] if prior else mode,
                    }
            if prior and kind == "file" and before != decode(prior["after"]):
                raise ValueError(f"managed file edited; preserved: {path}")
            if not prior and before is not None and kind == "file":
                raise ValueError(f"refusing to overwrite unmanaged file: {path}")
            if (
                not prior
                and kind == "shell"
                and before is not None
                and shell.START.encode() in before
            ):
                raise ValueError(f"unowned guard PATH block; preserved: {path}")
            if kind in {"shell", "desktop"} and before is not None:
                mode = stat.S_IMODE(path.stat().st_mode)
            preserve_config = kind == "config" and (
                (prior and (prior.get("preserve", False) or before != decode(prior["after"])))
                or (not prior and before is not None)
            )
            records[key] = {
                "before": prior["before"] if prior else encode(before),
                "after": encode(data),
                "kind": kind,
                "mode": prior["mode"]
                if prior
                else (stat.S_IMODE(path.stat().st_mode) if before is not None else mode),
            }
            if kind == "config":
                records[key]["preserve"] = bool(preserve_config)
            tx.write(path, data, mode)

        mac_service = backend("service") if plan.get("mac_app") else None
        mac_loaded = bool(mac_service and mac_service.status(paths))
        if mac_loaded and not old.get("mac_app"):
            raise ValueError("unowned monitor is already loaded")
        mac_touched = False
        service_touched = False
        try:
            write(paths.guard_config, plan["config_data"], kind="config")
            package = Path(__file__).parent
            for source in sorted(package.rglob("*.py")):
                write(
                    paths.root / "lib/claude_geo_guard" / source.relative_to(package),
                    source.read_bytes(),
                    kind="package",
                )
            bootstrap = (
                "import sys\nfrom pathlib import Path\n"
                "sys.path.insert(0, str(Path(__file__).parent))\n"
                "from claude_geo_guard.paths import Paths\n"
                f"Paths.pin({str(paths.home)!r}, {str(paths.data)!r}, "
                f"{str(paths.config)!r}, {str(paths.state)!r}, "
                f"{str(paths.runtime) if paths.runtime else None!r})\n"
                "from claude_geo_guard.cli import main\nraise SystemExit(main())\n"
            )
            write(paths.root / "lib/bootstrap.py", bootstrap.encode())
            for name, subcommand in (("claude-geo-guard", ""), ("claude", " code --")):
                script = (
                    "#!/bin/sh\n# CLAUDE_GEO_GUARD_WRAPPER\n"
                    + (hook_fast_path(paths) if not subcommand else "")
                    + "exec "
                    + shlex.quote(sys.executable)
                    + " -I -B "
                    + shlex.quote(str(paths.root / "lib/bootstrap.py"))
                    + subcommand
                    + ' "$@"\n'
                )
                write(paths.bin / name, script.encode(), 0o755)
            write(paths.settings, json_bytes(plan["settings"]), kind="settings")
            settings_manager.parse(read(paths.settings))
            for release in plan.get("released_desktops", []):
                target = Path(release["target"])
                if read(target) != release["current"].encode():
                    raise ValueError(f"desktop entry changed since inspection; retry: {target}")
                data = None if release["data"] is None else release["data"].encode()
                tx.write(target, data, stat.S_IMODE(target.stat().st_mode))
                records.pop(str(target), None)
            for desktop in plan["desktops"]:
                write(Path(desktop["target"]), desktop["after"].encode(), 0o644, "desktop")
                if shutil.which("desktop-file-validate"):
                    subprocess.run(
                        ["desktop-file-validate", desktop["target"]],
                        check=True,
                        capture_output=True,
                        timeout=5,
                    )
            managed_block = shell.block(paths.bin)
            zdot = Path(os.environ.get("ZDOTDIR") or paths.home)
            rc_files = [
                paths.home / ".bashrc",
                paths.home / ".profile",
                zdot / ".zshrc",
                zdot / ".zprofile",
            ]
            rc_files += [
                paths.home / name
                for name in (".bash_profile", ".bash_login")
                if (paths.home / name).exists()
            ]
            for rc in dict.fromkeys(rc_files):
                raw = read(rc)
                changed = shell.add(raw.decode() if raw is not None else "", managed_block)
                write(rc, changed.encode(), kind="shell")
            if plan["desktops"]:
                write(
                    paths.config / "systemd/user" / systemd.UNIT,
                    systemd.unit(guard).encode(),
                    0o644,
                )
            if mac_service:
                app = backend("application")
                if app.identify(Path(plan["mac_app"]["path"])) != plan["mac_app"]:
                    raise ValueError("Claude.app changed during installation")
                app.build(paths, write)
                write(mac_service.plist(paths), mac_service.contents(paths))
            manifest = {
                "schema": 2,
                "mac_app": plan.get("mac_app"),
                "files": records,
                "groups": plan["groups"],
                "original_settings": old.get("original_settings", encode(plan["settings_raw"])),
                "desktops": plan["desktops"],
                "binaries": plan["binaries"],
                "launchers": plan.get("launchers", []),
                "shell_block": managed_block,
                "claude_binary": plan["claude_binary"],
                "code_version": plan["version"],
            }
            tx.write(paths.manifest, json_bytes(manifest))
            if plan["desktops"]:
                service_touched = True
                systemd.run("daemon-reload")
                systemd.run("enable", systemd.UNIT)
                systemd.run("restart", systemd.UNIT)
                if not systemd.active():
                    raise ValueError("monitor did not become active")
            if mac_service:
                mac_touched = True
                mac_service.stop(paths)
                mac_service.start(paths)
            # Verify launchers are executable/importable without network or app launches.
            subprocess.run([str(guard), "--version"], capture_output=True, check=True, timeout=5)
        except BaseException:
            if service_touched:
                systemd.best_effort("stop", systemd.UNIT)
                if not was_enabled:
                    systemd.best_effort("disable", systemd.UNIT)
            try:
                if mac_touched:
                    mac_service.stop(paths)
            finally:
                conflicts = tx.rollback()
                if mac_touched and mac_loaded:
                    mac_service.start(paths)
            if service_touched:
                systemd.best_effort("daemon-reload")
                if was_active:
                    systemd.best_effort("start", systemd.UNIT)
            if conflicts:
                print(
                    f"Rollback conflicts preserved: {conflicts}; recovery journal: {tx.backup}",
                    file=sys.stderr,
                )
            raise
        result = summary(plan)
        if replaced:
            result["replaced_files"] = replaced
            result["backup_journal"] = str(tx.backup)
        return result


def uninstall(paths):
    with lock(paths.manifest.parent / "install.lock"):
        manifest = load_manifest(paths)
        if not manifest:
            return {"uninstalled": True, "message": "no managed installation"}
        systemd = backend("systemd") if manifest["desktops"] else None
        desktop_entries = backend("desktop_entries") if manifest["desktops"] else None
        guard = paths.bin / "claude-geo-guard"
        originals = settings_manager.parse(decode(manifest["original_settings"]))
        current = settings_manager.parse(read(paths.settings))
        clean = settings_manager.remove(current, manifest["groups"], guard, originals)
        changes = {}
        desktop_map = {d["target"]: d for d in manifest["desktops"]}
        # Plan ALL removals before stopping the monitor. Any conflict aborts safely.
        for filename, record in manifest["files"].items():
            path = Path(filename)
            raw = read(path)
            before = decode(record["before"])
            if record["kind"] == "settings":
                target = (
                    decode(manifest["original_settings"])
                    if clean == originals
                    else json_bytes(clean)
                )
            elif raw is None:
                continue
            elif record["kind"] == "config":
                if record["preserve"] or raw != decode(record["after"]):
                    continue  # Keep preexisting or user-edited preferences.
                target = before
            elif record["kind"] == "shell":
                target = shell.remove(raw.decode(), manifest["shell_block"]).encode()
                if not target and before is None:
                    target = None
            elif record["kind"] == "package":
                target = before
            elif record["kind"] == "desktop":
                if raw == decode(record["after"]):
                    restored = desktop_map[filename]["restore_text"]
                    target = restored.encode("utf-8") if restored is not None else None
                else:
                    target = desktop_entries.restore(
                        raw.decode(), desktop_map[filename]["edits"]
                    ).encode()
            else:
                if raw != decode(record["after"]):
                    raise ValueError(
                        f"managed file edited; preserved: {path}. Reconcile before uninstalling"
                    )
                target = before
            changes[path] = (target, record["mode"])
        mac_service = backend("service") if manifest.get("mac_app") else None
        mac_loaded = bool(mac_service and mac_service.status(paths))
        has_service = bool(manifest["desktops"])
        was_active = systemd.active() if has_service else False
        was_enabled = (
            systemd.run("is-enabled", systemd.UNIT, check=False).returncode == 0
            if has_service
            else False
        )
        tx = Transaction(journal_path(paths))
        try:
            if has_service:
                systemd.run("disable", "--now", systemd.UNIT)
            if mac_loaded:
                mac_service.stop(paths)
            for path, (data, mode) in changes.items():
                tx.write(path, data, mode)
            tx.write(paths.manifest, None)
            if has_service:
                systemd.run("daemon-reload")
        except BaseException:
            tx.rollback()
            if mac_loaded and not mac_service.status(paths):
                mac_service.start(paths)
            if has_service:
                systemd.best_effort("daemon-reload")
                if was_enabled:
                    systemd.best_effort("enable", systemd.UNIT)
                if was_active:
                    systemd.best_effort("start", systemd.UNIT)
            raise
        if mac_service:
            bundle = backend("application").destination(paths)
            directories = {
                parent
                for path in changes
                for parent in path.parents
                if parent == bundle or bundle in parent.parents
            }
            for directory in sorted(directories, key=lambda d: len(d.parts), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass
        return {
            "uninstalled": True,
            "backups_retained": str(paths.manifest.parent / "backups"),
            "message": "Open a new shell or run hash -r / rehash to clear cached command paths",
        }


def _update_setting(paths, key, value):
    with lock(paths.manifest.parent / "install.lock"):
        manifest = load_manifest(paths)
        if not manifest or manifest.get("schema") != 2:
            raise ValueError("install the guard first with ./install.sh --yes")
        raw = read(paths.guard_config)
        preferences = config.load(paths)
        if preferences[key] != value:
            preferences[key] = value
            preferences = config.validate(preferences)
            tx = Transaction(journal_path(paths))
            try:
                if read(paths.guard_config) != raw:
                    raise ValueError("configuration changed since inspection; retry")
                tx.write(paths.guard_config, json_bytes(preferences))
            except BaseException:
                tx.rollback()
                raise


def set_enabled(paths, enabled):
    _update_setting(paths, "enabled", enabled)


def set_country(paths, country):
    """Change only the policy; existing hooks, launchers and services need no rewrite."""
    country = normalize_country(country)
    _update_setting(paths, "allowed_country", country)
    return {
        "allowed_country": country,
        "message": "Applies at the next launch, hook or monitor check; no restart required",
    }


def set_timezone(paths, timezone):
    timezone = normalize_timezone(timezone)
    _update_setting(paths, "timezone", timezone)
    return {
        "timezone": timezone,
        "message": "Applies to new guarded Claude processes; fully quit and relaunch Desktop "
        "and start new CLI sessions. System timezone is unchanged.",
    }
