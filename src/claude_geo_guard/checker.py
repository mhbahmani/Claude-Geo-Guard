"""Fail-closed policy engine. curl supplies bounded, family-specific, verified TLS."""

import ipaddress
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from .platforms import snapshot
from . import config, dns_guard
from .policy import COUNTRIES, normalize_country

TRACE_HOSTS = ("claude.ai", "api.anthropic.com")
ENDPOINT = "https://ifconfig.io/all.json"
# curl exits for a dropped connection or timeout, which say nothing about the egress country.
TRANSIENT_EXITS = {7, 28, 35, 52, 55, 56}
# Hooks time out after 30 seconds; a retry must fit inside this overall budget.
DEADLINE_SECONDS = 20
RETRY_SECONDS = 9


@dataclass
class Verdict:
    allowed: bool
    reason: str
    countries: dict[int, str]
    allowed_country: str | None = None

    ips: dict[int, str] = field(default_factory=dict)
    allowed_ips: list[str] = field(default_factory=list)
    errors: dict[int, str] = field(default_factory=dict)

    observations: dict = field(default_factory=dict)

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Egress:
    country: str
    ip: str


class ProviderFailure(ValueError):
    """Safe diagnostic and validated partial results; never raw body/stderr."""

    def __init__(self, message, *, country=None, ip=None, transient=False):
        super().__init__(message)
        self.country, self.ip, self.transient = country, ip, transient


def _http_status(output):
    status = (output or b"").rpartition(b"\n")[2]
    if len(status) == 3 and status.isdigit() and 100 <= int(status) <= 599:
        return status.decode("ascii")
    return None


def query(family: int, host=None) -> Egress:
    if host is not None and host not in TRACE_HOSTS:
        raise ValueError("unknown trace host")
    try:
        result = subprocess.run(
            [
                "curl",
                "-q",
                f"-{family}",
                "--noproxy",
                "*",
                "--proto",
                "=https",
                "--connect-timeout",
                "2",
                "--max-time",
                "6",
                "--max-filesize",
                "4096",
                "--fail",
                "--silent",
                "--show-error",
                "--write-out",
                "\n%{http_code}",
                "--header",
                "Authorization:",
                "--header",
                "Cookie:",
                "--header",
                "Proxy-Authorization:",
                "--header",
                "x-api-key:",
                ENDPOINT if host is None else f"https://{host}/cdn-cgi/trace",
            ],
            capture_output=True,
            env={"PATH": os.environ.get("PATH", os.defpath), "LC_ALL": "C"},
            stdin=subprocess.DEVNULL,
            timeout=8,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        reasons = {
            5: "proxy DNS lookup failed",
            6: "provider DNS lookup failed",
            7: "could not connect to provider",
            22: "provider returned an HTTP error",
            28: "provider request timed out",
            35: "TLS handshake failed",
            52: "provider returned no response",
            55: "sending the request failed",
            56: "receiving the response failed",
            60: "TLS certificate verification failed",
            63: "provider response exceeded size limit",
            77: "CA certificate file unavailable",
        }
        reason = reasons.get(error.returncode, "provider request failed")
        status = _http_status(error.stdout)
        context = f"curl exit {error.returncode}" + (f", HTTP {status}" if status else "")
        raise ProviderFailure(
            f"IPv{family}: {reason} ({context})", transient=error.returncode in TRANSIENT_EXITS
        ) from None
    except subprocess.TimeoutExpired:
        raise ProviderFailure(
            f"IPv{family}: verification command exceeded 8-second deadline", transient=True
        ) from None
    except OSError as error:
        raise ProviderFailure(
            f"IPv{family}: cannot execute curl ({type(error).__name__})"
        ) from None
    body, separator, status = result.stdout.rpartition(b"\n")
    if not separator or status != b"200":
        raise ProviderFailure(
            f"IPv{family}: provider did not return HTTP 200 (received {_http_status(result.stdout) or 'invalid status'})"
        )
    if len(body) > 4096:
        raise ProviderFailure(f"IPv{family}: provider response exceeded size limit")
    if host is not None:
        try:
            fields = _unique_response(
                line.split("=", 1) for line in body.decode("ascii").splitlines()
            )
        except ValueError:
            raise ProviderFailure(f"IPv{family}: malformed edge trace") from None
        if fields.get("h") != host:
            raise ProviderFailure(f"IPv{family}: unexpected trace hostname")
        data = {"country_code": fields.get("loc"), "ip": fields.get("ip")}
    else:
        try:
            data = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=_unique_response,
                parse_constant=_reject_constant,
            )
        except (ValueError, UnicodeDecodeError):
            raise ProviderFailure(f"IPv{family}: provider returned invalid JSON") from None
        if not isinstance(data, dict):
            raise ProviderFailure(f"IPv{family}: provider response is not an object")
    raw_country = data.get("country_code")
    country = raw_country.strip().upper() if isinstance(raw_country, str) else None
    country = country if country in COUNTRIES else None
    ip = None
    try:
        address = ipaddress.ip_address(config.normalize_ip(data.get("ip")))
        if address.version == family and address.is_global and not address.is_multicast:
            ip = str(address)
    except ValueError:
        pass
    if country is None or ip is None:
        raise ProviderFailure(
            f"IPv{family}: provider returned an invalid country or public IP address",
            country=country,
            ip=ip,
        )
    return Egress(country, ip)


def _reject_constant(_):
    raise ValueError("nonfinite JSON constant")


def _unique_response(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("duplicate provider key")
        data[key] = value
    return data


def check(*, routes=snapshot, request=None, environ=None, allowed_country=None) -> Verdict:
    started = time.monotonic()
    try:
        preferences = config.load()
        target = (
            preferences["allowed_country"]
            if allowed_country is None
            else normalize_country(allowed_country)
        )
        allowed_ips = preferences["allowed_ips"]
    except (OSError, ValueError) as error:
        return Verdict(False, f"guard configuration is invalid or unreadable: {error}", {})
    countries, ips, errors = {}, {}, {}

    observations = {}

    def verdict(allowed, reason):
        return Verdict(allowed, reason, countries, target, ips, allowed_ips, errors, observations)

    env = os.environ if environ is None else environ
    if any(
        env.get(k)
        for k in (
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
        )
    ):
        return verdict(False, "proxy environment is ambiguous; use a routed VPN")
    try:
        try:
            dns_guard.check_hosts()
        except (ValueError, OSError):
            return verdict(False, "protected domain override or unreadable /etc/hosts")
        before = routes()
        if not before.families:
            return verdict(False, "no usable public routes")
        hosts = [*TRACE_HOSTS] if preferences["split_tunnel"] else [*TRACE_HOSTS, None]
        keys = [
            (family, host)
            for family in before.families
            for host in (hosts if request is None else [None])
        ]
        failures = {}

        def collect(pending):
            transient = {}
            for (family, host), task in pending.items():
                provider = host or "ifconfig.io"
                try:
                    result = task.result()
                    if (
                        not isinstance(result, Egress)
                        or not isinstance(result.country, str)
                        or result.country not in COUNTRIES
                    ):
                        raise ValueError("invalid country result")
                    address = ipaddress.ip_address(config.normalize_ip(result.ip))
                    if address.version != family or not address.is_global or address.is_multicast:
                        raise ValueError("invalid public IP result")
                    failures.pop((family, host), None)
                    observations.setdefault(provider, {})[family] = {
                        "country": result.country,
                        "ip": str(address),
                    }
                    if (
                        family not in countries
                        or result.country != target
                        or (allowed_ips and str(address) not in allowed_ips)
                    ):
                        countries[family], ips[family] = result.country, str(address)
                except ProviderFailure as error:
                    if error.country is not None:
                        countries[family] = error.country
                    if error.ip is not None:
                        ips[family] = error.ip
                    failures[family, host] = f"{provider}: {error}"
                    transient[family, host] = error.transient
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                    failures[family, host] = (
                        f"IPv{family}: verification failed ({type(error).__name__})"
                    )
                    transient[family, host] = isinstance(
                        error, (TimeoutError, subprocess.TimeoutExpired)
                    )
            return transient

        def submit(pool, key):
            family, host = key
            if request is None:
                return pool.submit(query, family, host)
            return pool.submit(request, family)

        with ThreadPoolExecutor(max_workers=6) as pool:
            transient = collect({key: submit(pool, key) for key in keys})
            mismatch = any(country != target for country in countries.values()) or (
                allowed_ips and any(ip not in allowed_ips for ip in ips.values())
            )
            # One retry for a dropped request, never for a confirmed mismatch or a hard error.
            if (
                transient
                and all(transient.values())
                and not mismatch
                and time.monotonic() - started + RETRY_SECONDS <= DEADLINE_SECONDS
            ):
                collect({key: submit(pool, key) for key in transient})
        for (family, _host), message in failures.items():
            errors[family] = message
        if errors:
            return verdict(False, "verification failed: " + "; ".join(errors.values()))
        dns_guard.check_hosts()
        if before != routes():
            return verdict(False, "network changed during verification")
        current = config.load()
        if (
            (allowed_country is None and current["allowed_country"] != target)
            or current["allowed_ips"] != allowed_ips
            or current["split_tunnel"] != preferences["split_tunnel"]
        ):
            return verdict(False, "country or IP policy changed during verification; retry")
        if any(o["country"] != target for group in observations.values() for o in group.values()):
            return verdict(False, f"egress outside configured country {target}")
        if allowed_ips and any(
            o["ip"] not in allowed_ips for group in observations.values() for o in group.values()
        ):
            return verdict(False, "public IP does not match configured allowed_ips")
        return verdict(
            True,
            f"all usable address families verified {target}"
            + (" and allowed IPs" if allowed_ips else ""),
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        return verdict(False, f"verification failed ({type(error).__name__})")
