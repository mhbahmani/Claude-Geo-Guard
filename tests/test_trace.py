import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from claude_geo_guard import checker, config, dns_guard
from claude_geo_guard.storage import atomic_write, json_bytes


@pytest.mark.parametrize(
    "split,general,api,allowed",
    [
        (False, "DE", "GB", False),
        (True, "DE", "GB", True),
        (True, "GB", "DE", False),
        (False, "GB", "DE", False),
        (False, "GB", "GB", True),
    ],
)
def test_independent_destinations(isolated, monkeypatch, split, general, api, allowed):
    atomic_write(isolated.guard_config, json_bytes(dict(config.DEFAULTS, split_tunnel=split)))

    def query(family, host):
        return checker.Egress(
            {None: general, "api.anthropic.com": api, "claude.ai": "GB"}[host], "1.1.1.1"
        )

    monkeypatch.setattr(checker, "query", query)
    result = checker.check(
        routes=lambda: SimpleNamespace(families=(4,), fingerprint="test"), environ={}
    )
    assert result.allowed is allowed
    assert set(result.observations) == (
        set(checker.TRACE_HOSTS) if split else {*checker.TRACE_HOSTS, "ifconfig.io"}
    )


@pytest.mark.parametrize(
    "body,valid",
    [
        ("h=claude.ai\nip=1.1.1.1\nloc=GB\ncolo=LHR", True),
        ("h=claude.ai\nip=1.1.1.1\ncolo=LHR", False),
        ("h=other.test\nip=1.1.1.1\nloc=GB", False),
        ("h=claude.ai\nip=1.1.1.1\nloc=GB\nloc=US", False),
        ("h=claude.ai\nip=127.0.0.1\nloc=GB", False),
    ],
)
def test_anonymous_trace(monkeypatch, body, valid):
    for key in (
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "SSLKEYLOGFILE",
        "CURL_HOME",
        "HOME",
    ):
        monkeypatch.setenv(key, "private-fixture")
    run = Mock(return_value=subprocess.CompletedProcess([], 0, body.encode() + b"\n200"))
    monkeypatch.setattr(subprocess, "run", run)
    if valid:
        assert checker.query(4, "claude.ai") == checker.Egress("GB", "1.1.1.1")
    else:
        with pytest.raises(checker.ProviderFailure):
            checker.query(4, "claude.ai")
    args = run.call_args.args[0]
    assert args[:2] == ["curl", "-q"]
    assert {"Authorization:", "Cookie:", "x-api-key:"} <= set(args)
    assert args[-1] == "https://claude.ai/cdn-cgi/trace" and "--location" not in args
    assert set(run.call_args.kwargs["env"]) == {"PATH", "LC_ALL"}


def test_hosts_file_aliases_and_comments(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost # api.anthropic.com\n::1 notclaude.ai\n")
    dns_guard.check_hosts(hosts)
    hosts.write_text("1.1.1.1 unrelated API.ANTHROPIC.COM. # pinned\n")
    with pytest.raises(ValueError):
        dns_guard.check_hosts(hosts)


def test_hosts_override_denies_before_any_probe(monkeypatch):
    monkeypatch.setattr(dns_guard, "check_hosts", Mock(side_effect=ValueError("override")))
    probe = Mock()
    result = checker.check(request=probe, environ={})
    assert not result.allowed and "/etc/hosts" in result.reason
    probe.assert_not_called()
