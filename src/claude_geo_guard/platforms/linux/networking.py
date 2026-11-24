"""Route discovery without sending probe packets; no public addresses are persisted."""

import ipaddress
import json
import os
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class NetworkState:
    families: tuple[int, ...]
    fingerprint: str


def _only_local_ipv6(routes):
    """An unreachable VPN default can yield EHOSTUNREACH instead of ENETUNREACH."""
    for route in routes:
        if route.get("type") in {"unreachable", "local", "multicast"}:
            continue
        dst = route.get("dst", "default")
        if dst == "default" or not ipaddress.IPv6Network(dst, strict=False).is_link_local:
            return False
    return True


def snapshot() -> NetworkState:
    parts = []
    families = []
    for family, target in ((4, "1.1.1.1"), (6, "2606:4700:4700::1111")):
        for what in (("route", "show", "table", "all"), ("rule", "show"), ("address", "show")):
            result = subprocess.run(
                ["ip", "-j", f"-{family}", *what],
                capture_output=True,
                text=True,
                timeout=1,
                check=True,
            )
            # Drop volatile lifetimes/counters: route/interface/source changes still invalidate.
            data = json.loads(result.stdout)

            def stable(value):
                if isinstance(value, dict):
                    return {
                        k: stable(v)
                        for k, v in value.items()
                        if k not in {"valid_life_time", "preferred_life_time", "expires", "cache"}
                    }
                if isinstance(value, list):
                    return [stable(v) for v in value]
                return value

            if what[0] == "route":
                route_table = data
            parts.append(stable(data))
        route = subprocess.run(
            ["ip", "-j", f"-{family}", "route", "get", target, "uid", str(os.getuid())],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if route.returncode:
            if (
                family == 6
                and route.stderr.strip() == "RTNETLINK answers: No route to host"
                and _only_local_ipv6(route_table)
            ):
                continue
            if "Network is unreachable" not in route.stderr:
                raise RuntimeError(f"IPv{family} route lookup failed")
            continue
        records = json.loads(route.stdout)
        if not records or any(
            r.get("type") in {"unreachable", "blackhole", "prohibit"}
            or r.get("dev") == "lo"
            or not r.get("dev")
            for r in records
        ):
            raise RuntimeError(f"IPv{family} route is ambiguous")
        families.append(family)
        parts.append(stable(records))
    return NetworkState(tuple(families), json.dumps(parts, sort_keys=True))
