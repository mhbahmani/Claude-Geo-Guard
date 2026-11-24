import subprocess

def _egress_lines(verdict):
    families = sorted(verdict.countries.keys() | verdict.ips.keys() | verdict.errors.keys())
    if not families:
        return ["Detected country / IP: Not verified · IP not returned"]
    return [
        (
            f"Detected IPv{family}: "
            f"{verdict.countries.get(family, 'Not verified')} · "
            f"{verdict.ips.get(family, 'IP not returned')}"
        )
        for family in families
    ]


def describe(error, *, runtime=False):
    """Build a useful native message without exposing Python container syntax."""
    heading = "Claude Desktop couldn’t open"
    explanation = "The guard could not complete its launch checks."
    next_step = "Check your connection, then open Claude again."
    facts = []

    verdict = getattr(error, "verdict", None)
    if verdict is not None:
        heading = "Claude was closed by the guard" if runtime else "Couldn’t verify your connection"
        explanation = (
            "The guard could not verify the connection, so Claude was closed."
            if runtime
            else "The guard could not verify the connection, so Claude stayed closed."
        )
        if verdict.reason.startswith("egress outside configured country "):
            explanation = (
                "The connection does not match the allowed country, so Claude "
                + ("was closed." if runtime else "stayed closed.")
            )
            next_step = "Use a connection in the allowed country, then open Claude again."
        elif "does not match configured allowed_ips" in verdict.reason:
            explanation = (
                "The public IP is not allowed, so Claude "
                + ("was closed." if runtime else "stayed closed.")
            )
            next_step = "Check the connection and allowed_ips setting, then open Claude again."
        if verdict.allowed_country:
            facts.append(f"Allowed country: {verdict.allowed_country}")
        facts.extend(_egress_lines(verdict))
        facts.append(f"IP filter: {'Enabled' if verdict.allowed_ips else 'Off'}")

    sections = [heading, explanation]
    if facts:
        sections.append("\n".join(facts))
    sections.extend((next_step, f"Technical details: {error}"))
    return "\n\n".join(sections)


def available():
    return "osascript"


def present(script, message, stop=None):
    try:
        with subprocess.Popen(
            ["/usr/bin/osascript", "-e", "on run argv", "-e", script, "-e", "end run", message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ) as proc:
            while True:
                try:
                    return proc.wait(timeout=0.25) == 0
                except subprocess.TimeoutExpired:
                    if stop and stop.is_set():
                        proc.kill()
                        return False
    except OSError:
        return False


def show_error(error, runtime=False, stop=None):
    message = describe(error, runtime=runtime)
    return present(
        'display alert "Claude Guard" message (item 1 of argv) as critical giving up after 60',
        message,
        stop,
    )


def notify(message):
    present('display notification (item 1 of argv) with title "Claude Guard"', message)
