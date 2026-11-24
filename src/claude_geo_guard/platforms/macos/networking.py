import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class NetworkState:
    families: tuple
    fingerprint: tuple


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=2, env={"LC_ALL": "C"})


def snapshot():
    proxy = run("/usr/sbin/scutil", "--proxy")
    proxy.check_returncode()
    if not re.fullmatch(r"<dictionary>\s*\{.*\}\s*", proxy.stdout, re.S) or re.search(
        r"Enable\s*:\s*(?!0\s*$)\S+", proxy.stdout, re.M
    ):
        raise ValueError("ambiguous system proxy")
    state = [proxy.stdout]
    families = []
    for family, target in ((4, "1.1.1.1"), (6, "2606:4700:4700::1111")):
        result = run("/sbin/route", "-n", "get", "-inet" if family == 4 else "-inet6", target)
        if result.returncode and "not in table" in result.stderr:
            continue
        result.check_returncode()
        interface = re.search(r"^\s*interface:\s*([\w.]+)\s*$", result.stdout, re.M)
        if (
            not interface
            or interface[1].startswith("lo")
            or re.search(r"\b(REJECT|BLACKHOLE)\b", result.stdout)
        ):
            raise ValueError("ambiguous route")
        details = run("/sbin/ifconfig", interface[1])
        details.check_returncode()
        if "UP" not in details.stdout or not re.search(
            r"^\s*inet" + ("6" if family == 6 else "") + r"\s", details.stdout, re.M
        ):
            raise ValueError("ambiguous interface")
        state.extend((result.stdout.partition("recvpipe")[0], details.stdout))
        families.append(family)
    return NetworkState(tuple(families), tuple(state))
