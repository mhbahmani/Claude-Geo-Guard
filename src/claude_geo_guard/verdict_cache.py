"""The latest connection verdict, shared by the monitor and concurrent hooks.

One line that the hook wrapper reads without starting Python:
"<expiry epoch seconds> <allow|unverified|deny> <message>". A denial is never
reused later; it only answers hooks that were already waiting for that check.
"""

import os
import tempfile
import time
from pathlib import Path

from .checker import DEADLINE_SECONDS
from .storage import lock

HOOK_SECONDS = 15
# The monitor's next check starts one interval after the previous one finished.
MONITOR_SLACK_SECONDS = DEADLINE_SECONDS
PROXIES = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
HOSTS = Path("/etc/hosts")
STATES = ("allow", "unverified", "deny")


def state(verdict):
    if verdict.allowed:
        return "allow"
    return "unverified" if verdict.transient else "deny"


def message(verdict):
    target = verdict.allowed_country or "your allowed country"
    detected = sorted(set(verdict.countries.values()) - {verdict.allowed_country})
    if detected:
        cause = f"the connection is outside {target} (detected {', '.join(detected)})"
        advice = f"Try again once the connection is back in {target}."
    else:
        cause, advice = verdict.reason, "Try again once this is resolved."
    return f"Claude Geo Guard stopped Claude before anything was sent: {cause}. {advice}"


def store(paths, verdict, seconds):
    kind = state(verdict)
    expires = int(time.time()) + (0 if kind == "deny" else seconds)
    text = " ".join(message(verdict).split()) if kind == "deny" else ""
    _write(paths.verdict, f"{expires} {kind} {text}".rstrip() + "\n")


def clear(paths):
    try:
        paths.verdict.unlink(missing_ok=True)
    except OSError:
        pass


def _write(path, text):
    # A disposable cache: no fsync, so frequent monitor updates cost no disk flushes.
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".verdict-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read(paths):
    """(state, message, written) of the stored verdict, or None when it is unusable."""
    try:
        info = paths.verdict.stat()
        for source in (paths.guard_config, HOSTS):
            try:
                if source.stat().st_mtime_ns >= info.st_mtime_ns:
                    return None  # Policy or name resolution changed after this check.
            except FileNotFoundError:
                return None
        expires, kind, *text = paths.verdict.read_text().rstrip("\n").split(" ", 2)
        expires = int(expires)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if kind not in STATES:
        return None
    return kind, (text[0] if text else ""), info.st_mtime, expires


def current(paths, checker, environ=None):
    """(state, message) for a hook; parallel hooks share one check."""
    env = os.environ if environ is None else environ
    if any(env.get(name) for name in PROXIES):
        verdict = checker()  # Denied locally from this process's environment, no request.
        return state(verdict), message(verdict)
    entry = read(paths)
    if entry is not None and entry[3] > time.time():
        return entry[:2]
    started = time.time()
    with lock(paths.verdict.with_name("verdict.lock")):
        entry = read(paths)
        if entry is not None and (entry[3] > time.time() or entry[2] >= started):
            return entry[:2]
        verdict = checker()
        try:
            store(paths, verdict, HOOK_SECONDS)
        except OSError:
            pass
        return state(verdict), message(verdict)
