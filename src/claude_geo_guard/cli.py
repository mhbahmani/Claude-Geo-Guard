import argparse
import json
import subprocess
import sys

from . import (
    __version__,
    cli_launcher,
    desktop_launcher,
    installer,
    monitor,
)
from .checker import check
from .platforms import backend
from .diagnostics import diagnostics
from .paths import Paths


def parser():
    root = argparse.ArgumentParser(description="Claude Geo Guard — best-effort checkpoints")
    root.add_argument("--version", action="version", version=__version__)
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("hook", help=argparse.SUPPRESS)
    sub.add_parser("status")
    sub.add_parser("doctor")
    install = sub.add_parser("install")
    install.add_argument("--dry-run", action="store_true")
    install.add_argument("--yes", action="store_true")
    install.add_argument("--claude-binary", type=str)
    install.add_argument("--desktop-app", help="absolute path to the Claude.app bundle")
    install.add_argument(
        "--country",
        metavar="CODE",
        help="allowed country code (default GB on first install; retained on reinstall)",
    )
    country = sub.add_parser("set-country", help="change the installed allowed country")
    country.add_argument("country", metavar="CODE")
    install.add_argument(
        "--timezone",
        metavar="ZONE",
        help="IANA timezone for Claude, e.g. Europe/London; inherit clears override (default: inherit; retained on reinstall)",
    )
    timezone = sub.add_parser(
        "set-timezone", help="set the timezone for new guarded Claude processes"
    )
    timezone.add_argument("timezone", metavar="ZONE", help="IANA timezone or inherit")
    install.add_argument(
        "--skip-desktop", action="store_true", help="explicitly install CLI/hooks only"
    )
    install.add_argument(
        "--interval", type=float, help="monitor seconds, 5..300 (default 15; retained on reinstall)"
    )
    install.add_argument(
        "--grace",
        type=float,
        help="termination grace seconds, 0..30 (default 5; retained on reinstall)",
    )
    sub.add_parser("uninstall")
    mon = sub.add_parser("monitor")
    mon.add_argument("--interval", type=float, help="override config interval for this invocation")
    mon.add_argument("--grace", type=float, help="override config grace for this invocation")
    for name in ("code", "desktop"):
        launch = sub.add_parser(name, help=argparse.SUPPRESS)
        launch.add_argument("args", nargs=argparse.REMAINDER)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    paths = Paths.current()
    try:
        backend("environment")
        if args.command in {"check", "hook"}:
            verdict = check()
            if args.command == "check":
                print(json.dumps(verdict.as_dict()))
            elif not verdict.allowed:
                print(f"Claude Geo Guard denied: {verdict.reason}", file=sys.stderr)
            return 0 if verdict.allowed else 2
        if args.command in {"status", "doctor"}:
            report = diagnostics(paths)
            print(json.dumps(report, indent=2))
            return (
                0
                if report["healthy_configuration"] and report["country_verification"]["allowed"]
                else 2
            )
        if args.command == "install":
            plan = installer.preflight(
                paths,
                claude_binary=args.claude_binary,
                skip_desktop=args.skip_desktop,
                interval=args.interval,
                grace=args.grace,
                country=args.country,
                timezone=args.timezone,
                desktop_app=args.desktop_app,
            )
            print(json.dumps(installer.summary(plan), indent=2))
            if args.dry_run:
                return 0
            if not args.yes and (
                not sys.stdin.isatty()
                or input("Apply these user-local changes? [y/N] ").lower() != "y"
            ):
                raise ValueError("installation canceled; use --yes for unattended installation")
            installer.install(paths, plan)
            print("Installed; configuration checked. Real desktop/hook behavior NOT VERIFIED.")
            print(installer.LIMITATION)
            print(
                f"Backups: {paths.manifest.parent / 'backups'}; open a new shell; run claude-geo-guard doctor"
            )
        elif args.command == "set-country":
            print(json.dumps(installer.set_country(paths, args.country), indent=2))
        elif args.command == "set-timezone":
            print(json.dumps(installer.set_timezone(paths, args.timezone), indent=2))
        elif args.command == "uninstall":
            print(json.dumps(installer.uninstall(paths), indent=2))
        elif args.command == "monitor":
            monitor.run(paths, args.interval, args.grace)
        elif args.command in {"code", "desktop"}:
            forwarded = args.args[1:] if args.args[:1] == ["--"] else args.args
            if args.command == "desktop":
                desktop_launcher.launch(paths, forwarded)
            else:
                manifest = installer.load_manifest(paths) or {}
                cli_launcher.launch(paths, forwarded, manifest.get("claude_binary"))
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Claude Geo Guard: {error}", file=sys.stderr)
        if args.command == "desktop" and not backend("dialogs").show_error(error):
            print(
                (
                    "Could not show a desktop error window. Install zenity or kdialog."
                    if sys.platform == "linux"
                    else "Could not show a desktop error window; check the graphical session."
                ),
                file=sys.stderr,
            )
        return 2
    except KeyboardInterrupt:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
