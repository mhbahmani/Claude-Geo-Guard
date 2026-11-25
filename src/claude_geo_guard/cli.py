import argparse
import json
import os
import subprocess
import sys

from . import (
    __version__,
    cli_launcher,
    config,
    desktop_launcher,
    installer,
    install_prompts,
    history,
    monitor,
    reports,
    shell,
)
from .checker import check
from .platforms import backend
from .diagnostics import diagnostics
from .paths import Paths


class CommandParser(argparse.ArgumentParser):
    def format_help(self):
        return f"""Claude Geo Guard  {__version__}
Geolocation checks for Claude Code and Desktop.

Usage: claude-geo-guard <command> [options]

Inspect
  check             Verify your current connection
  doctor            Diagnose installation and protection
  last-stop         Explain the last recorded monitor closure
  logs              Read runtime monitor logs

Manage
  enable / disable  Turn protection on or off
  set-country CODE  Change the allowed country
  set-timezone ZONE Set the timezone for new Claude sessions
  install           Install or repair protection
  uninstall         Remove the installation

Service
  monitor           Run the runtime monitor in the foreground
  version           Show the installed version

Examples
  claude-geo-guard logs -f             Follow monitor events live
  claude-geo-guard logs --since today  Review today's events
  claude-geo-guard check --json        Get a machine-readable verdict

Options
  -h, --help        Show this guide
  --version         Show the installed version

Run claude-geo-guard <command> --help for command options.
"""


def nonnegative(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a nonnegative integer") from None
    if number < 0:
        raise argparse.ArgumentTypeError("expected a nonnegative integer")
    return number


def show_logs(args):
    if sys.platform != "linux":
        raise ValueError("logs currently requires Linux and a systemd user journal")
    command = [
        "journalctl",
        "--user",
        "--unit=claude-geo-guard.service",
        f"--lines={args.lines}",
        "--output=json" if args.json else "--output=short-iso",
    ]
    if args.follow:
        command.append("--follow")
    if args.since:
        command.extend(["--since", args.since])
    if args.no_pager or args.follow or not sys.stdout.isatty():
        command.append("--no-pager")
    try:
        return subprocess.run(command).returncode
    except KeyboardInterrupt:
        return 0


def parser():
    root = CommandParser(prog="claude-geo-guard", description="Claude Geo Guard")
    root.add_argument("--version", action="version", version=__version__)
    sub = root.add_subparsers(dest="command", parser_class=argparse.ArgumentParser)
    logs = sub.add_parser("logs", description="Read the runtime monitor's systemd user journal.")
    logs.add_argument("-f", "--follow", action="store_true", help="follow new events; Ctrl-C exits")
    logs.add_argument(
        "-n",
        "--lines",
        type=nonnegative,
        default=100,
        metavar="N",
        help="number of recent entries (default: 100)",
    )
    logs.add_argument("--since", metavar="TIME", help="start time, e.g. today or '10 minutes ago'")
    logs.add_argument("--no-pager", action="store_true", help="print directly without a pager")
    logs.add_argument("--json", action="store_true", help="output journal entries as JSON")
    sub.add_parser("version", help="show the Claude Geo Guard version")
    for name in ("check", "doctor"):
        sub.add_parser(name).add_argument(
            "--json", action="store_true", help="print the full JSON report"
        )
    sub.add_parser("hook", help=argparse.SUPPRESS)
    sub.add_parser("last-stop", help="explain the last recorded monitor closure")
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


def _cursor_hook_response():
    # Claude launched inside Cursor can inherit editor variables. Only Cursor's
    # own hook envelope carries its version at the top level of the JSON input.
    version = os.environ.get("CURSOR_VERSION")
    if not version or sys.stdin.isatty():
        return None
    try:
        event = json.load(sys.stdin)
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(event, dict) or event.get("cursor_version") != version:
        return None
    if event.get("hook_event_name") == "beforeSubmitPrompt":
        return {"continue": True}
    if event.get("hook_event_name") == "preToolUse":
        return {"permission": "allow"}
    return None


def install_help():
    print("\nUseful commands (open a new terminal):")
    print("  claude-geo-guard check      Verify your connection")
    print("  claude-geo-guard last-stop  Explain the last monitor closure (Linux)")
    print("  claude-geo-guard logs -f    Follow monitor events (Linux)")


def main(argv=None):
    root = parser()
    args = root.parse_args(argv)
    if args.command is None:
        root.print_help()
        return 0
    if args.command == "version":
        print(__version__)
        return 0
    paths = Paths.current()
    try:
        if args.command == "logs":
            return show_logs(args)
        if args.command == "last-stop":
            print(history.report(history.last_stop()))
            return 0
        backend("environment")
        if args.command in {"check", "hook"}:
            if args.command == "hook":
                response = _cursor_hook_response()
                if response is not None:
                    print(json.dumps(response))
                    return 0
                if not config.load(paths)["enabled"]:
                    return 0
            verdict = check()
            if args.command == "check":
                result = verdict.as_dict()
                print(json.dumps(result) if args.json else reports.connection(result))
            elif verdict.transient:
                print(
                    f"Claude Geo Guard cannot verify the connection (offline or unreachable): {verdict.reason}",
                    file=sys.stderr,
                )
            elif not verdict.allowed:
                print(f"Claude Geo Guard denied: {verdict.reason}", file=sys.stderr)
            return 0 if verdict.allowed else 2
        if args.command == "doctor":
            report = diagnostics(paths)
            print(json.dumps(report, indent=2) if args.json else reports.doctor(report))
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
                install_help()
                return 0
            print("Installed; configuration checked. Real desktop/hook behavior NOT VERIFIED.")
            print(installer.LIMITATION)
            print(f"Backups: {paths.manifest.parent / 'backups'}")
            install_help()
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
