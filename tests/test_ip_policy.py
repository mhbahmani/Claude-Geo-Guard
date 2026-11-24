from claude_geo_guard.platforms.linux import systemd
import json
import subprocess
from unittest.mock import Mock

import pytest

from claude_geo_guard import checker, config, desktop_launcher, monitor
from claude_geo_guard.platforms.linux import dialogs
from claude_geo_guard.platforms.linux.networking import NetworkState
from claude_geo_guard.storage import atomic_write, json_bytes

IP4, IP6 = "1.1.1.1", "2606:4700:4700::1111"


def save(paths, **changes):
    atomic_write(paths.guard_config, json_bytes(dict(config.DEFAULTS, **changes)))


@pytest.mark.parametrize(
    "allowed_ips, expected",
    [([], True), ([IP4, IP6], True), ([IP4], False), (["8.8.8.8", IP6], False)],
)
def test_dual_stack_country_and_optional_exact_ips(isolated, allowed_ips, expected):
    save(isolated, allowed_ips=allowed_ips)
    result = checker.check(
        routes=lambda: NetworkState((4, 6), "same"),
        request=lambda family: checker.Egress("GB", IP4 if family == 4 else IP6),
        environ={},
    )
    assert result.allowed == expected
    assert result.ips == {4: IP4, 6: IP6}  # Returned even when IP matching is off or denies.
    assert result.countries == {4: "GB", 6: "GB"}


@pytest.mark.parametrize("family,ip", [(4, IP4), (6, IP6)])
def test_single_family_exact_ip(isolated, family, ip):
    save(isolated, allowed_ips=[ip])
    assert checker.check(
        routes=lambda: NetworkState((family,), "same"),
        request=lambda _: checker.Egress("GB", ip),
        environ={},
    ).allowed


def test_country_still_required_when_ip_matches(isolated):
    save(isolated, allowed_ips=[IP4])
    result = checker.check(
        routes=lambda: NetworkState((4,), "same"),
        request=lambda _: checker.Egress("PL", IP4),
        environ={},
    )
    assert not result.allowed and "country" in result.reason
    assert result.ips == {4: IP4}


def test_ip_policy_change_during_check_denied(isolated):
    save(isolated)

    def request(_):
        save(isolated, allowed_ips=["8.8.8.8"])
        return checker.Egress("GB", IP4)

    result = checker.check(routes=lambda: NetworkState((4,), "same"), request=request, environ={})
    assert not result.allowed and "policy changed" in result.reason


@pytest.mark.parametrize("value", [None, IP4, ["bad"], ["1.1.1.1/32"], ["fe80::1%eth0"], [7]])
def test_invalid_optional_ip_matcher_denies(isolated, value):
    save(isolated, allowed_ips=value)
    request = Mock()
    assert not checker.check(request=request, environ={}).allowed
    request.assert_not_called()


def test_ipv6_canonicalization_and_duplicate_removal(isolated):
    save(isolated, allowed_ips=["2606:4700:4700:0:0:0:0:1111", IP6])
    assert config.load(isolated)["allowed_ips"] == [IP6]


@pytest.mark.parametrize(
    "payload",
    [
        b"{}",
        b"[]",
        b"{",
        b"GB",
        b'{"country_code":"GB","ip":"1.1.1.1","port":NaN}',
        b"\xff",
        b"x" * 4097,
        b'{"country_code":"GB","ip":"1.1.1.1","ip":"8.8.8.8"}',
        b'{"country_code":"GB","ip":"127.0.0.1"}',
        b'{"country_code":"GB","ip":"2606:4700:4700::1111"}',
    ],
)
def test_invalid_json_response_or_wrong_ip_family(payload, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, payload + b"\n200"))
    )
    with pytest.raises(checker.ProviderFailure):
        checker.query(4)


def test_country_and_ip_from_one_request(monkeypatch):
    body = json.dumps(
        {"country_code": " gb ", "ip": IP4, "forwarded": "private-unused-value"}
    ).encode()
    run = Mock(return_value=subprocess.CompletedProcess([], 0, body + b"\n200"))
    monkeypatch.setattr(subprocess, "run", run)
    assert checker.query(4) == checker.Egress("GB", IP4)
    assert run.call_count == 1 and run.call_args.args[0][-1] == "https://ifconfig.io/all.json"


def test_partial_country_failure_retains_valid_returned_ip(monkeypatch):
    body = json.dumps({"country_code": "invalid", "ip": IP4}).encode()
    monkeypatch.setattr(
        subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, body + b"\n200"))
    )
    result = checker.check(
        routes=lambda: NetworkState((4,), "same"), request=checker.query, environ={}
    )
    assert not result.allowed and result.ips == {4: IP4} and not result.countries
    error = desktop_launcher.LaunchBlocked(result.reason, kind="verification", verdict=result)
    assert f"Not verified · {IP4}" in dialogs.describe(error).plain()


def test_failed_first_family_does_not_hide_second_family():
    def request(family):
        if family == 4:
            raise checker.ProviderFailure("IPv4: provider request timed out")
        return checker.Egress("GB", IP6)

    result = checker.check(routes=lambda: NetworkState((4, 6), "same"), request=request, environ={})
    assert not result.allowed and result.ips == {6: IP6}
    message = dialogs.describe(
        desktop_launcher.LaunchBlocked(result.reason, kind="verification", verdict=result)
    )
    assert ("Detected IPv4", "Not verified · IP not returned") in message.facts
    assert ("Detected IPv6", f"GB · {IP6}") in message.facts


@pytest.mark.parametrize("allowed", [True, False])
def test_display_returned_ip_without_matcher(allowed, monkeypatch):
    verdict = checker.Verdict(allowed, "test", {4: "GB"}, "GB", {4: IP4})
    if allowed:
        run = Mock()
        monkeypatch.setattr(subprocess, "run", run)
        dialogs.show_success(verdict)
        assert IP4 in run.call_args.args[0][-1]
        assert "Country matches your setting: GB" in run.call_args.args[0][-1]
    else:
        assert (
            IP4
            in dialogs.describe(
                desktop_launcher.LaunchBlocked("test", kind="verification", verdict=verdict)
            ).plain()
        )


@pytest.mark.parametrize("allowed", [True, False])
def test_monitor_never_shows_success_messages(monkeypatch, allowed):
    from claude_geo_guard.platforms.linux.processes import Process

    show = Mock()
    monkeypatch.setattr(dialogs, "show_success", show)
    monitor.tick(
        set(),
        scanner=lambda *_: [Process(42, 1, 1, "/opt/test")],
        checker=lambda: checker.Verdict(allowed, "test", {4: "GB"}, "GB", {4: IP4}),
        killer=Mock(),
        notifier=Mock(),
    )
    show.assert_not_called()


@pytest.mark.parametrize("enabled", [True, False])
def test_only_enabled_guarded_launch_shows_success(environment, monkeypatch, enabled):
    paths = environment["paths"]
    save(paths, success_notifications=enabled)
    atomic_write(paths.manifest, json_bytes({"binaries": [str(environment["desktop"])]}))
    monkeypatch.setattr(systemd, "active", lambda: True)
    monkeypatch.setattr(
        desktop_launcher, "check", lambda: checker.Verdict(True, "test", {4: "GB"}, "GB", {4: IP4})
    )
    monkeypatch.setattr(desktop_launcher.os, "execve", Mock())
    show = Mock()
    monkeypatch.setattr(dialogs, "show_success", show)
    desktop_launcher.launch(paths, [str(environment["desktop"])])
    assert show.call_count == int(enabled)


def test_notification_failure_never_changes_access(monkeypatch):
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=FileNotFoundError()))
    dialogs.show_success(checker.Verdict(True, "verified", {4: "GB"}, "GB", {4: IP4}))
