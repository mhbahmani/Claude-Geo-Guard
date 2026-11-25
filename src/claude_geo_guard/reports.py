"""Plain-text terminal reports for manual checks and installation diagnostics."""


def connection(verdict):
    title = "Connection check passed" if verdict["allowed"] else "Connection check failed"
    lines = [title, f"  {verdict['reason']}"]
    if verdict["allowed_country"]:
        lines.append(f"  Allowed country: {verdict['allowed_country']}")
    if verdict["allowed_ips"]:
        lines.append("  Allowed IPs: " + ", ".join(verdict["allowed_ips"]))

    rows = []
    for provider, observations in sorted(verdict["observations"].items()):
        for family, result in sorted(observations.items()):
            rows.append((provider, f"IPv{family}", result["country"], result["ip"]))
    if rows:
        rows.insert(0, ("Destination", "Route", "Country", "Public IP"))
        widths = [max(map(len, column)) for column in zip(*rows)]
        lines.append("")
        for row in rows:
            lines.append("  " + "  ".join(v.ljust(w) for v, w in zip(row, widths)).rstrip())
    for family, error in sorted(verdict["errors"].items()):
        details = " · ".join(
            val for val in (verdict["countries"].get(family), verdict["ips"].get(family)) if val
        )
        lines.append(f"  IPv{family} error: {error}" + (f" ({details})" if details else ""))
    return "\n".join(lines)


def doctor(report):
    healthy = report["healthy_configuration"] and report["country_verification"]["allowed"]
    lines = ["Claude Geo Guard doctor — " + ("healthy" if healthy else "needs attention")]
    if report["issues"]:
        lines.extend(["", "Issues", *("  - " + issue for issue in report["issues"])])
    settings = report.get("configuration", {})
    protection = "Unknown"
    if "enabled" in settings:
        protection = "Enabled" if settings["enabled"] else "Disabled"
    lines.extend(
        [
            "",
            connection(report["country_verification"]),
            "",
            "Installation",
            "  Installed: " + ("Yes" if report["installed"] else "No"),
            f"  Protection: {protection}",
            f"  Config: {report['config_file']}",
            "  CLI wrapper on PATH: " + ("Yes" if report["path_wrapper_effective"] else "No"),
            "  Hooks registered: " + ("Yes" if report.get("hooks_registered") else "No"),
            "  Desktop monitor: " + ("Active" if report["monitor_active"] else "Inactive"),
        ]
    )
    if report.get("original_cli"):
        lines.append(f"  Claude Code: {report['claude_code_version']} ({report['original_cli']})")
    if report.get("guarded_application"):
        lines.append(f"  Desktop launcher: {report['guarded_application']}")
    for launcher in report["desktop_launcher_guards"]:
        lines.append(f"  Desktop launcher: {launcher['id']} ({launcher['configuration']})")
    return "\n".join(lines)
