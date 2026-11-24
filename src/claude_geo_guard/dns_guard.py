"""Reject local overrides for first-party Claude domains before contacting them."""

from pathlib import Path

DOMAINS = ("claude.ai", "claude.com", "anthropic.com")


def check_hosts(path=Path("/etc/hosts")):
    for line in path.read_bytes().splitlines():
        fields = line.partition(b"#")[0].split()
        for alias in fields[1:]:
            name = alias.lower().rstrip(b".")
            for domain in DOMAINS:
                suffix = domain.encode("ascii")
                if name == suffix or name.endswith(b"." + suffix):
                    raise ValueError("protected domain overridden in /etc/hosts")
