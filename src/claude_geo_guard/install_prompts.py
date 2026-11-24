"""Interactive installer questions. All writes still go through installer.install."""

import sys
from pathlib import Path

from . import cli_launcher, config, installer
from .policy import normalize_country
from .storage import read
from .timezone import normalize_timezone


def ask(label, default, parse=str):
    while True:
        try:
            answer = input(f"{label} [{default}]: ").strip()
        except EOFError:
            raise ValueError("installation canceled: input closed") from None
        try:
            return parse(answer or str(default))
        except ValueError as error:
            print(error, file=sys.stderr)


def yes_no(label, default):
    def parse(value):
        if value.lower() in {"y", "yes"}:
            return True
        if value.lower() in {"n", "no"}:
            return False
        raise ValueError("Enter yes or no.")

    return ask(label, "yes" if default else "no", parse)


def preferences(paths, args):
    raw = read(paths.guard_config)
    saved = config.parse(raw) if raw is not None else config.DEFAULTS
    old = installer.load_manifest(paths)
    print("Claude Geo Guard setup — press Enter to use each value in brackets.")
    if old and raw is None:
        print("The saved configuration is missing. The brackets show first-install defaults.")
    if args.country is None:
        args.country = ask(
            "Allowed country (two-letter code)", saved["allowed_country"], normalize_country
        )
    if args.timezone is None:
        args.timezone = ask(
            "Timezone for new Claude processes",
            saved["timezone"] or "inherit",
            lambda value: normalize_timezone(value) or "inherit",
        )
    if args.split_tunnel is None:
        args.split_tunnel = yes_no(
            "Use split tunneling (check only Claude destinations)?", saved["split_tunnel"]
        )

    def addresses(value):
        return (
            []
            if value.lower() == "none"
            else [config.normalize_ip(item) for item in value.split(",")]
        )

    options = {
        "allowed_ips": ask(
            "Allowed public IPs (comma-separated, or none for country only)",
            ", ".join(saved["allowed_ips"]) or "none",
            addresses,
        )
    }
    if args.claude_binary is None:
        try:
            cli_launcher.discover(paths.bin, (old or {}).get("claude_binary"))
        except ValueError:
            args.claude_binary = input(
                "Claude Code was not found. Enter its absolute executable path: "
            ).strip()
    if not args.skip_desktop and not args.desktop_app:
        if old and (old["desktops"] or old.get("mac_app")):
            print("Keeping the installed Desktop protection.")
        else:
            default_desktop = old is None
            if sys.platform == "darwin":
                default_desktop = default_desktop and any(
                    p.is_dir()
                    for p in (
                        Path("/Applications/Claude.app"),
                        paths.home / "Applications/Claude.app",
                    )
                )
            args.skip_desktop = not yes_no("Include Desktop protection?", default_desktop)
    if not args.skip_desktop:
        if sys.platform == "darwin" and not args.desktop_app:
            location = ask(
                "Claude.app path", ((old or {}).get("mac_app") or {}).get("path") or "auto"
            )
            args.desktop_app = None if location == "auto" else location
        for key, label in (
            ("interval", "Monitor interval in seconds (5–300)"),
            ("grace", "Termination grace in seconds (0–30)"),
        ):
            if getattr(args, key) is None:
                setattr(
                    args,
                    key,
                    ask(label, saved[key], lambda value: config.validate_timing(key, float(value))),
                )
        options["success_notifications"] = yes_no(
            "Show a notification for successful Desktop launches?", saved["success_notifications"]
        )
    return raw, options


def preview(plan):
    print(f"\nInstall for {plan['allowed_country']} using {plan['binary']} ({plan['version']}).")
    mode = (
        "Claude destinations only"
        if plan["split_tunnel"]
        else "Claude destinations and general egress"
    )
    print(
        f"Network checks: {mode}. Allowed IPs: {', '.join(plan['allowed_ips']) or 'any matching the country'}."
    )
    print(f"Timezone: {plan['timezone'] or 'inherit'}.")
    print(f"Configuration: {plan['config_path']}")
    print("Install the CLI wrapper, register Claude Code hooks, and update shell PATH entries.")
    if plan["mac_app"] or plan["desktops"]:
        targets = (
            [plan["mac_app"]["path"]]
            if plan["mac_app"]
            else [item["target"] for item in plan["desktops"]]
        )
        print("Desktop: " + ", ".join(targets))
        print(
            f"Start the monitor (interval {plan['interval']:g}s, termination grace {plan['grace']:g}s). A failed check can close a running Claude Desktop."
        )
    else:
        print("Desktop monitoring will be off.")
