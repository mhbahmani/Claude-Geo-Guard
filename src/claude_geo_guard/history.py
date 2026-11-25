"""Read closure evidence from the Linux monitor journal without running a new check."""

import json
import subprocess
import sys
from datetime import datetime, timezone


def last_stop():
    if sys.platform != "linux":
        raise ValueError("last-stop currently requires Linux and a systemd user journal")
    result = subprocess.run(
        [
            "journalctl",
            "--user",
            "--unit=claude-geo-guard.service",
            "--reverse",
            "--grep=runtime_stop_requested",
            "--lines=50",
            "--output=json",
            "--no-pager",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    # journalctl uses 1 for a grep with no matches; real errors have diagnostics.
    if result.returncode and (result.returncode != 1 or result.stderr.strip()):
        raise ValueError(
            "could not read the monitor journal; run claude-geo-guard logs for details"
        )
    for line in result.stdout.splitlines():
        try:
            entry = json.loads(line)
            if not isinstance(entry, dict):
                continue
            event = json.loads(entry.get("MESSAGE", ""))
            if not isinstance(event, dict) or event.get("event") != "runtime_stop_requested":
                continue
            if not isinstance(event.get("reason"), str):
                continue
            try:
                when = (
                    datetime.fromtimestamp(
                        int(entry["__REALTIME_TIMESTAMP"]) / 1_000_000, timezone.utc
                    )
                    .astimezone()
                    .isoformat(timespec="seconds")
                )
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                when = "Time unavailable"
            return {**event, "time": when}
        except (ValueError, TypeError):
            continue
    if result.stdout.strip():
        raise ValueError(
            "closure records could not be parsed; run claude-geo-guard logs for details"
        )
    return None


def _text(value):
    # Journal text must not inject terminal control sequences into the report.
    return "".join(c if c.isprintable() else " " for c in str(value))


def report(event):
    if event is None:
        return (
            "No recorded monitor closure found\n"
            "  The retained user journal contains no closure request.\n"
            "  Older entries may have expired; this does not cover crashes or manual exits.\n\n"
            "Inspect monitor events: claude-geo-guard logs"
        )
    countries = event.get("countries")
    observed = (
        ", ".join(f"IPv{_text(family)}: {_text(country)}" for family, country in countries.items())
        if isinstance(countries, dict) and countries
        else "Not verified"
    )
    lines = [
        "Last monitor closure",
        f"  When:             {_text(event['time'])}",
        f"  Allowed country:  {_text(event.get('allowed_country') or 'Not recorded')}",
        f"  Observed country: {observed}",
        f"  Processes:        {_text(event.get('process_count', 'Not recorded'))}",
        f"  Grace period:     {_text(event.get('grace_seconds', 'Not recorded'))} seconds",
        "",
        "Reason",
        f"  {_text(event['reason'])}",
        "",
        "This records a termination request, not confirmation that every process exited.",
        "",
        "Next steps",
        "  claude-geo-guard check    Verify the connection now",
        "  claude-geo-guard logs -f  Follow monitor events",
    ]
    return "\n".join(lines)
