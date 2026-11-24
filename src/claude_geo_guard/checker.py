"""Fail-closed policy engine. curl supplies bounded, family-specific, verified TLS."""

import ipaddress
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from .platforms import snapshot
from . import config
from .policy import COUNTRIES, normalize_country

ENDPOINT = "https://ifconfig.io/all.json"


@dataclass
class Verdict:
    allowed: bool
    reason: str
    countries: dict[int, str]
    allowed_country: str | None = None

    ips: dict[int, str] = field(default_factory=dict)
    allowed_ips: list[str] = field(default_factory=list)
    errors: dict[int, str] = field(default_factory=dict)

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Egress:
    country: str
    ip: str


class ProviderFailure(ValueError):
    """Safe diagnostic and validated partial results; never raw body/stderr."""

    def __init__(self, message, *, country=None, ip=None):
        super().__init__(message)
        self.country, self.ip = country, ip


def _http_status(output):
    status = (output or b"").rpartition(b"\n")[2]
    if len(status) == 3 and status.isdigit() and 100 <= int(status) <= 599:
        return status.decode("ascii")
    return None


def query(family: int) -> Egress:
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
                ENDPOINT,
            ],
            capture_output=True,
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
        raise ProviderFailure(f"IPv{family}: {reason} ({context})") from None
    except subprocess.TimeoutExpired:
        raise ProviderFailure(
            f"IPv{family}: verification command exceeded 8-second deadline"
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
        if address.version == family and address.is_global:
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


def check(*, routes=snapshot, request=query, environ=None, allowed_country=None) -> Verdict:
    try:
        preferences = config.load()
        target = (
            preferences["allowed_country"]
            if allowed_country is None
            else normalize_country(allowed_country)
        )
        allowed_ips = preferences["allowed_ips"]
    except (OSError, ValueError):
        return Verdict(
            False, "guard configuration is invalid or unreadable; repair config.json", {}
        )
    countries, ips, errors = {}, {}, {}

    def verdict(allowed, reason):
        return Verdict(allowed, reason, countries, target, ips, allowed_ips, errors)

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
        before = routes()
        if not before.families:
            return verdict(False, "no usable public routes")
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = {family: pool.submit(request, family) for family in before.families}
            for family, task in pending.items():
                try:
                    result = task.result()
                    if (
                        not isinstance(result, Egress)
                        or not isinstance(result.country, str)
                        or result.country not in COUNTRIES
                    ):
                        raise ValueError("invalid country result")
                    address = ipaddress.ip_address(config.normalize_ip(result.ip))
                    if address.version != family or not address.is_global:
                        raise ValueError("invalid public IP result")
                    countries[family], ips[family] = result.country, str(address)
                except ProviderFailure as error:
                    if error.country is not None:
                        countries[family] = error.country
                    if error.ip is not None:
                        ips[family] = error.ip
                    errors[family] = str(error)
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                    errors[family] = f"IPv{family}: verification failed ({type(error).__name__})"
        if errors:
            return verdict(False, "verification failed: " + "; ".join(errors.values()))
        if before != routes():
            return verdict(False, "network changed during verification")
        current = config.load()
        if (allowed_country is None and current["allowed_country"] != target) or current[
            "allowed_ips"
        ] != allowed_ips:
            return verdict(False, "country or IP policy changed during verification; retry")
        if any(country != target for country in countries.values()):
            return verdict(False, f"egress outside configured country {target}")
        if allowed_ips and any(ip not in allowed_ips for ip in ips.values()):
            return verdict(False, "public IP does not match configured allowed_ips")
        return verdict(
            True,
            f"all usable address families verified {target}"
            + (" and allowed IPs" if allowed_ips else ""),
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        return verdict(False, f"verification failed ({type(error).__name__})")
