"""Small user-editable JSON configuration, separate from installer state."""

import ipaddress
import json
import math

from .paths import Paths
from .policy import DEFAULT_COUNTRY, normalize_country
from .storage import read
from .timezone import normalize_timezone

DEFAULTS = {
    "allowed_country": DEFAULT_COUNTRY,
    "timezone": None,
    "interval": 15,
    "grace": 5,
    "allowed_ips": [],
    "success_notifications": True,
}
REQUIRED = {"allowed_country", "timezone", "interval", "grace"}


def normalize_ip(value):
    if not isinstance(value, str) or "%" in value:
        raise ValueError(
            "allowed_ips must contain exact IPv4/IPv6 addresses without scopes or CIDR ranges"
        )
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        raise ValueError(
            "allowed_ips must contain exact IPv4/IPv6 addresses without scopes or CIDR ranges"
        ) from None


def validate_timing(key, value):
    lower, upper = (5, 300) if key == "interval" else (0, 30)
    if type(value) not in (int, float) or not lower <= value <= upper or not math.isfinite(value):
        raise ValueError(f"{key} must be a finite number from {lower} to {upper} seconds")
    return value


def validate(data):
    if not isinstance(data, dict):
        raise ValueError("config.json must contain a JSON object")
    missing, unknown = REQUIRED - data.keys(), data.keys() - DEFAULTS.keys()
    if missing:
        raise ValueError("config.json is missing: " + ", ".join(sorted(missing)))
    if unknown:
        raise ValueError(
            "config.json has unknown keys; use allowed_country, timezone, interval, grace, allowed_ips, success_notifications"
        )
    allowed_ips = data.get("allowed_ips", [])
    if not isinstance(allowed_ips, list):
        raise ValueError(
            "allowed_ips must be an array of exact addresses, or [] to disable IP matching"
        )
    notifications = data.get("success_notifications", True)
    if not isinstance(notifications, bool):
        raise ValueError("success_notifications must be true or false (Desktop launches only)")
    return {
        "allowed_country": normalize_country(data["allowed_country"]),
        "timezone": normalize_timezone(data["timezone"]),
        "interval": validate_timing("interval", data["interval"]),
        "grace": validate_timing("grace", data["grace"]),
        "allowed_ips": sorted(set(normalize_ip(value) for value in allowed_ips)),
        "success_notifications": notifications,
    }


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("config.json contains a duplicate key")
        result[key] = value
    return result


def parse(raw):
    try:
        return validate(json.loads(raw.decode("utf-8"), object_pairs_hook=_unique))
    except json.JSONDecodeError as error:
        raise ValueError(
            f"config.json has invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error
    except UnicodeDecodeError as error:
        raise ValueError(
            "config.json is not valid UTF-8 JSON; fix it without resetting installer state"
        ) from error


def load(paths=None):
    paths = paths or Paths.current()
    raw = read(paths.guard_config)
    if raw is not None:
        return parse(raw)
    if paths.manifest.exists() or (paths.bin / "claude-geo-guard").exists():
        raise ValueError(
            f"configuration is missing: {paths.guard_config}; restore it before using Claude"
        )
    return dict(DEFAULTS)
