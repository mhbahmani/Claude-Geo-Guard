import json
import socket
import ssl
import subprocess
from unittest.mock import Mock

import pytest

from claude_geo_guard.checker import Egress, check, query
from claude_geo_guard.platforms.linux.networking import NetworkState, snapshot


@pytest.mark.parametrize(
    "countries, allowed",
    [
        ({4: "GB"}, True),
        ({6: "GB"}, True),
        ({4: "US"}, False),
        ({6: "FR"}, False),
        ({4: "GB", 6: "GB"}, True),
        ({4: "GB", 6: "US"}, False),
        ({4: "US", 6: "GB"}, False),
        ({}, False),
    ],
)
def test_family_policy(countries, allowed):
    state = NetworkState(tuple(countries), "same")
    assert (
        check(
            routes=lambda: state,
            request=lambda family: Egress(
                countries[family], "1.1.1.1" if family == 4 else "2606:4700:4700::1111"
            ),
            environ={},
        ).allowed
        == allowed
    )


@pytest.mark.parametrize(
    "error",
    [
        socket.gaierror(),
        ssl.SSLError(),
        TimeoutError(),
        subprocess.TimeoutExpired("curl", 6),
        subprocess.CalledProcessError(22, "curl"),
        subprocess.CalledProcessError(7, "curl"),
        ValueError("bad"),
    ],
)
def test_failure_closed(error):
    request = Mock(side_effect=error)
    assert not check(
        routes=lambda: NetworkState((4, 6), "same"), request=request, environ={}
    ).allowed


@pytest.mark.parametrize(
    "raw,expected",
    [
        (b" GB\n", "GB"),
        (b"gb\r\n", "GB"),
        (b"US", "US"),
        (b"", None),
        (b"ZZ", None),
        (b"UK", None),
        (b"GB US", None),
        (b"<html>GB</html>", None),
        (b"GB\x00", None),
        (b"\xff", None),
        (b"G" * 65, None),
    ],
)
def test_response_validation(raw, expected, monkeypatch):
    run = Mock(
        return_value=subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "country_code": raw.decode("ascii", errors="replace"),
                    "ip": "2606:4700:4700::1111",
                }
            ).encode()
            + b"\n200",
            b"",
        )
    )
    monkeypatch.setattr(subprocess, "run", run)
    if expected:
        assert query(6) == Egress(expected, "2606:4700:4700::1111")
    else:
        with pytest.raises(ValueError):
            query(6)
    args = run.call_args.args[0]
    assert "-6" in args and "--insecure" not in args and "-k" not in args
    assert args[:2] == ["curl", "-q"] and "--max-time" in args and "--noproxy" in args


def test_connection_change():
    states = iter([NetworkState((4,), "a"), NetworkState((4,), "b")])
    result = check(
        routes=lambda: next(states), request=lambda _: Egress("GB", "1.1.1.1"), environ={}
    )
    assert not result.allowed and "changed" in result.reason


@pytest.mark.parametrize("key", ["http_proxy", "HTTPS_PROXY", "ALL_PROXY", "https_proxy"])
def test_proxy_denied_without_request(key):
    request = Mock()
    assert not check(request=request, environ={key: "http://proxy"}).allowed
    request.assert_not_called()


def test_real_adapter_with_fake_utilities(environment, monkeypatch):
    assert snapshot().families == (4,)
    monkeypatch.setenv("FAKE_IPV6", "1")
    assert snapshot().families == (4, 6)
    assert check().allowed
    (environment["bins"] / "FAKE_COUNTRY6").write_text("DE")
    assert not check().allowed


def test_route_error_not_absence(monkeypatch):
    def run(args, **kw):
        if "get" in args:
            return subprocess.CompletedProcess(args, 2, "", "permission denied")
        return subprocess.CompletedProcess(args, 0, "[]", "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError):
        snapshot()


@pytest.mark.parametrize("status", [b"301", b"403", b"500", b"000", b""])
def test_http_status_required(status, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 0, b"GB\n" + status, b"")),
    )
    with pytest.raises(ValueError):
        query(4)


def test_available_second_family_failure_denies_first_success():
    def request(family):
        if family == 6:
            raise TimeoutError()
        return Egress("GB", "1.1.1.1")

    assert not check(
        routes=lambda: NetworkState((4, 6), "same"), request=request, environ={}
    ).allowed


@pytest.mark.parametrize(
    "code,label",
    [
        (6, "DNS lookup failed"),
        (7, "could not connect"),
        (22, "HTTP error"),
        (28, "timed out"),
        (35, "TLS handshake failed"),
        (60, "certificate verification failed"),
        (63, "size limit"),
        (77, "CA certificate file"),
        (99, "provider request failed"),
    ],
)
def test_safe_provider_failure_diagnostics(monkeypatch, code, label):
    private = b"do-not-log 198.51.100.123 secret.example"
    monkeypatch.setattr(
        subprocess,
        "run",
        Mock(
            side_effect=subprocess.CalledProcessError(
                code,
                ["curl", "secret-argument"],
                output=private + b"\n429",
                stderr=private,
            )
        ),
    )
    verdict = check(routes=lambda: NetworkState((6,), "same"), request=query, environ={})
    assert not verdict.allowed
    assert "IPv6" in verdict.reason and label in verdict.reason
    assert f"curl exit {code}" in verdict.reason and "HTTP 429" in verdict.reason
    assert "198.51.100.123" not in verdict.reason and "secret" not in verdict.reason


@pytest.mark.parametrize(
    "error,label",
    [
        (subprocess.TimeoutExpired("private-command", 8, output=b"secret"), "8-second deadline"),
        (FileNotFoundError("private-path"), "cannot execute curl"),
    ],
)
def test_provider_execution_failure_is_safe(monkeypatch, error, label):
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=error))
    verdict = check(routes=lambda: NetworkState((4,), "same"), request=query, environ={})
    assert not verdict.allowed and label in verdict.reason
    assert "private" not in verdict.reason and "secret" not in verdict.reason


@pytest.mark.parametrize(
    "extra", [[], [{"dst": "default", "dev": "tun0"}], [{"dst": "2000::/3", "dev": "tun0"}]]
)
def test_linux_ipv6_host_unreachable_with_vpn_default(monkeypatch, extra):
    table = [{"type": "unreachable", "dst": "default"}, {"dst": "fe80::/64", "dev": "eth0"}] + extra

    def run(args, **kwargs):
        if "get" in args and "-6" in args:
            return subprocess.CompletedProcess(args, 2, "", "RTNETLINK answers: No route to host\n")
        data = [{"dev": "tun0"}] if "get" in args else table if "route" in args else []
        return subprocess.CompletedProcess(args, 0, json.dumps(data), "")

    monkeypatch.setattr(subprocess, "run", run)
    if extra:
        with pytest.raises(RuntimeError, match="IPv6 route lookup failed"):
            snapshot()
    else:
        assert snapshot().families == (4,)
