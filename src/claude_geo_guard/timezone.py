"""Optional TZ environment override for newly launched Claude processes."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def normalize_timezone(value):
    if value is None or value == "inherit":
        return None
    if not isinstance(value, str):
        raise ValueError("timezone must be an IANA name, e.g. Europe/London, or inherit")
    value = value.strip()
    if not value or value in {"localtime", "posixrules"} or value.startswith(("posix/", "right/")):
        raise ValueError("timezone must be an IANA name, e.g. Europe/London, or inherit")
    try:
        ZoneInfo.no_cache(value)
        # Claude's native runtime also needs the OS database, not just Python's fallback data.
        if not (Path("/usr/share/zoneinfo") / value).is_file():
            raise ZoneInfoNotFoundError(value)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError(
            "unknown or unavailable timezone; use an IANA name and install tzdata"
        ) from error
    return value


def load_timezone(paths):
    from .config import load

    return load(paths)["timezone"]


def launch_environment(paths):
    environment = os.environ.copy()
    timezone = load_timezone(paths)
    if timezone is not None:
        environment["TZ"] = timezone
        # Do not let an inherited alternate database reinterpret the selected name.
        environment.pop("TZDIR", None)
    return environment
