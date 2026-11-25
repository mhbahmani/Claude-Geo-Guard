import argparse
import json
import subprocess
import sys

from . import (
    __version__,
    cli_launcher,
    config,
    desktop_launcher,
    installer,
    install_prompts,
    monitor,
    shell,
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
    sub.add_parser("enable", help="enable guard protection")
    sub.add_parser("disable", help="disable guard protection")
    install = sub.add_parser("install")
    mode = install.add_mutually_exclusive_group()
    mode.add_argument(
        "--enabled", dest="enabled", action="store_true", help="enable protection immediately"
    )
    mode.add_argument(
        "--disabled", dest="enabled", action="store_false", help="install with protection disabled"
    )
    install.set_defaults(enabled=None)
    install.add_argument(
        "--split-tunnel",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="check only the two Claude destinations instead of also checking general egress",
    )
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
            if args.command == "hook" and not config.load(paths)["enabled"]:
                return 0
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
            interactive = not args.yes and not args.dry_run and sys.stdin.isatty()
            extra = {}
            if interactive:
                original_config, extra = install_prompts.preferences(paths, args)
            plan = installer.preflight(
                paths,
                claude_binary=args.claude_binary,
                skip_desktop=args.skip_desktop,
                interval=args.interval,
                grace=args.grace,
                country=args.country,
                split_tunnel=args.split_tunnel,
                timezone=args.timezone,
                desktop_app=args.desktop_app,
                enabled=args.enabled,
                **extra,
            )
            if interactive:
                if plan["config_raw"] != original_config:
                    raise ValueError("configuration changed during setup; retry")
                install_prompts.preview(plan)
            else:
                print(json.dumps(installer.summary(plan), indent=2))
            if args.dry_run:
                return 0
            if not args.yes and (
                not sys.stdin.isatty() or not install_prompts.yes_no("Apply these changes?", True)
            ):
                raise ValueError("installation canceled; use --yes for unattended installation")
            if interactive:
                print("\nInstalling Claude Geo Guard…", flush=True)
            installer.install(paths, plan)
            if not plan["enabled"]:
                print(config.DISABLED_MESSAGE)
            print(shell.routing_message(paths.bin / "claude"), file=sys.stderr)
            if interactive:
                install_prompts.heading("Installation complete")
                print(f"Backups: {paths.manifest.parent / 'backups'}")
                print("Open a new terminal and run: claude-geo-guard doctor")
                return 0
            print("Installed; configuration checked. Real desktop/hook behavior NOT VERIFIED.")
            print(installer.LIMITATION)
            print(
                f"Backups: {paths.manifest.parent / 'backups'}; open a new shell; run claude-geo-guard doctor"
            )
        elif args.command in {"enable", "disable"}:
            enabled = args.command == "enable"
            installer.set_enabled(paths, enabled)
            print("Claude Geo Guard is enabled." if enabled else config.DISABLED_MESSAGE)
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
        label = (
            "Claude Geo Guard blocked CLI launch" if args.command == "code" else "Claude Geo Guard"
        )
        print(f"{label}: {error}", file=sys.stderr)
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
    except (EOFError, KeyboardInterrupt):
        print("Installation or command canceled.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
