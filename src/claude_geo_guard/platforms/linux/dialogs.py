"""Native desktop failure messages. Presentation never changes the access decision."""

import html
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass

from ...desktop_launcher import LaunchBlocked

TITLE = "Claude Geo Guard"
DETAILS_LABEL = "Show details"


@dataclass(frozen=True)
class Message:
    heading: str
    explanation: str
    next_step: str
    facts: tuple[tuple[str, str], ...]
    details: str

    def plain(self):
        facts = "\n".join(f"{label}: {value}" for label, value in self.facts)
        return "\n\n".join(
            part for part in (self.heading, self.explanation, facts, self.next_step) if part
        )

    def markup(self, helper):
        """Only our structure is markup; every variable is escaped."""
        facts = "\n".join(
            f"{html.escape(label)}: <b>{html.escape(value)}</b>" for label, value in self.facts
        )
        heading = html.escape(self.heading)
        if helper == "zenity":
            heading = f'<span size="large" weight="bold">{heading}</span>'
        else:
            heading = f"<big><b>{heading}</b></big>"
        content = "\n\n".join(
            part
            for part in (heading, html.escape(self.explanation), facts, html.escape(self.next_step))
            if part
        )
        if helper == "zenity":
            # Zenity expands backslash escapes before parsing Pango markup.
            return content.replace("\\", "\\\\")
        return "<qt>" + content.replace("\n", "<br>") + "</qt>"


def egress_facts(verdict):
    families = sorted(verdict.countries.keys() | verdict.ips.keys() | verdict.errors.keys())
    return [
        (
            f"Detected IPv{family}",
            f"{verdict.countries.get(family, 'Not verified')} · {verdict.ips.get(family, 'IP not returned')}",
        )
        for family in families
    ] or [("Detected country / IP", "Not verified · IP not returned")]


def progress_command(message):
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    helper = available()
    if helper == "zenity":
        return [
            helper,
            "--progress",
            "--pulsate",
            "--no-cancel",
            f"--title={TITLE}",
            f"--text={message}",
        ]
    if helper:
        return [helper, "--title", TITLE, "--ok-label", "Hide", "--msgbox", message]
    return None


def show_warning(message):
    helper = available()
    if helper == "zenity":
        command = [helper, "--warning", "--no-markup", f"--title={TITLE}", f"--text={message}"]
    elif helper:
        command = [helper, "--title", TITLE, "--sorry", message]
    else:
        command = []
    try:
        if command and _dismissed(_run_dialog(command), helper):
            return
    except OSError:
        pass
    try:
        subprocess.run(["notify-send", TITLE, message], capture_output=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        pass


def show_success(verdict):
    """Open a timed confirmation alongside Claude in the graphical session."""
    helper = available()
    if not helper or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return
    lines = [f"Country matches your setting: {verdict.allowed_country}"]
    lines += [f"{label}: {value}" for label, value in egress_facts(verdict)]
    if verdict.allowed_ips:
        lines.append("Public IP also matches your allowed IPs.")
    message = "\n".join(lines)
    title = "Claude connection verified"
    if helper == "zenity":
        command = [
            helper,
            "--info",
            "--no-markup",
            "--ok-label=OK",
            "--timeout=5",
            f"--title={title}",
            f"--text={message}",
        ]
    else:
        command = ["timeout", "--foreground", "5s", helper, "--title", title, "--msgbox", message]
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def describe(error, *, runtime=False):
    """Keep the main view short; put raw errors and terminal commands in details."""
    heading = "Claude Desktop couldn’t open"
    explanation = "The guard could not complete its launch checks."
    next_step = "Open Show details for troubleshooting, then launch Claude again from Applications."
    facts = []
    if isinstance(error, LaunchBlocked):
        if error.kind == "monitor":
            heading = "Protection is not running"
            explanation = "Claude Desktop stayed closed because its runtime monitor is inactive."
            next_step = (
                "Restart the guard’s monitor, then open Claude again. See details for the command."
            )
        elif error.kind == "executable":
            heading = "The launcher needs an update"
            explanation = "Claude Desktop’s executable changed or could not be found."
            next_step = (
                "Rerun the guard installer to check the updated app, then open Claude again."
            )
        elif error.verdict is not None:
            verdict = error.verdict
            heading = "Couldn’t verify your connection"
            explanation = "Claude Desktop stayed closed because the country check could not finish."
            next_step = "Check your connection, then open Claude again from Applications."
            if verdict.reason.startswith("egress outside configured country "):
                heading = "Connection outside allowed country"
                explanation = "Claude Desktop stayed closed because your connection does not match your country setting."
                next_step = "Use a connection in the allowed country, then open Claude again from Applications."
            elif "does not match configured allowed_ips" in verdict.reason:
                heading = "Public IP is not allowed"
                explanation = "Claude Desktop stayed closed because the returned IP does not match your IP setting."
                next_step = "Check your connection and allowed_ips setting, then open Claude again."
            elif "proxy environment" in verdict.reason:
                explanation = (
                    "Proxy settings prevent a reliable country check. Claude Desktop stayed closed."
                )
                next_step = "Review your proxy settings. See details for troubleshooting."
            elif "network changed" in verdict.reason:
                explanation = "Your network changed during the check. Claude Desktop stayed closed."
                next_step = "Wait for your connection to settle, then open Claude again."
            elif "country policy" in verdict.reason or "guard configuration" in verdict.reason:
                heading = (
                    "Check your guard settings"
                    if "guard configuration" in verdict.reason
                    else "Check your country setting"
                )
                explanation = "The guard’s settings could not be read consistently. Claude Desktop stayed closed."
                next_step = "Review the guard’s configuration. See details for troubleshooting."
            if verdict.allowed_country:
                facts.append(("Allowed country", verdict.allowed_country))
            facts.extend(egress_facts(verdict))
            facts.append(("IP filter", "Enabled" if verdict.allowed_ips else "Off"))
            facts.append(("Country provider", "Claude edge traces (plus ifconfig.io in full mode)"))
    if runtime:
        heading = "Claude was closed by the guard"
        explanation = explanation.replace("stayed closed", "was closed")
        details = (
            f"Runtime protection stopped Claude\n\n{error}\n\n"
            + "\n".join(f"{label}: {value}" for label, value in facts)
            + "\n\nView the closure log:\n"
            "journalctl --user -u claude-geo-guard.service -n 50 --no-pager\n\n"
            "Check your current connection:\nclaude-geo-guard check"
        )
    else:
        details = f"Launch blocked\n\n{error}\n\nFor diagnostics, run:\nclaude-geo-guard doctor"
    if isinstance(error, LaunchBlocked) and error.verdict is not None and error.verdict.allowed_ips:
        details += "\n\nAllowed IPs:\n" + "\n".join(error.verdict.allowed_ips)
    return Message(heading, explanation, next_step, tuple(facts), details)


def available():
    """Return the preferred installed dialog helper, without opening a window."""
    names = (
        ("kdialog", "zenity")
        if "kde" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        else ("zenity", "kdialog")
    )
    return next((name for name in names if shutil.which(name)), None)


def _dismissed(result, helper):
    # KDialog uses 2 for closing the window; Zenity uses 1. Display failures
    # can share those statuses, but produce diagnostics on stderr.
    closed = (1, 2) if helper == "kdialog" else (1,)
    return result.returncode == 0 or (result.returncode in closed and not result.stderr)


def _runtime_environment():
    # A user service can start before login and keep an old display or Xauthority.
    env = os.environ.copy()
    keys = {
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
    }
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        for line in result.stdout.splitlines():
            key, _, value = line.partition("=")
            if key in keys:
                words = shlex.split(value)
                if len(words) == 1:
                    env[key] = words[0]
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return env


def _run_dialog(command, stop=None, env=None):
    if stop is None:
        return subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, env=env)
    # Runtime UI must exit with the monitor, including when started outside systemd.
    with subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    ) as child:
        while True:
            try:
                stdout, stderr = child.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if stop.is_set():
                    child.terminate()
                    try:
                        stdout, stderr = child.communicate(timeout=1)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        stdout, stderr = child.communicate()
                    break
        return subprocess.CompletedProcess(command, child.returncode, stdout, stderr)


def _notify_error(title, body, env):
    try:
        subprocess.run(
            [
                "notify-send",
                "--urgency=critical",
                title,
                html.escape(body),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=3,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def show_error(error, *, runtime=False, stop=None):
    """Show a summary with optional details; dismissal never retries or allows.

    Runtime callers use a background worker. Native widgets retain system
    themes, text scaling, focus behavior and keyboard dismissal. No shell is used.
    """
    message = describe(error, runtime=runtime)
    if stop is not None and stop.is_set():
        return False
    env = _runtime_environment() if runtime else os.environ
    if runtime:
        # Compositors may open a background service's dialog behind another window.
        _notify_error(message.heading, message.explanation, env)
    helper = available()
    if helper and (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        if helper == "zenity":
            command = [
                helper,
                "--error",
                *(["--modal"] if runtime else []),
                f"--title={TITLE}",
                "--width=440",
                "--ok-label=Close",
                f"--extra-button={DETAILS_LABEL}",
                f"--text={message.markup(helper)}",
            ]
        else:
            command = [
                helper,
                "--title",
                TITLE,
                "--ok-label",
                "Close",
                "--detailederror",
                message.markup(helper),
                "<qt>" + html.escape(message.details).replace("\n", "<br>") + "</qt>",
            ]
        try:
            # No timer dismisses a message while the user is still reading it.
            result = _run_dialog(command, stop, env)
            if (
                helper == "zenity"
                and not (stop is not None and stop.is_set())
                and result.returncode in (0, 1)
                and (result.stdout or b"").strip() == DETAILS_LABEL.encode()
            ):
                result = _run_dialog(
                    [
                        helper,
                        "--info",
                        f"--title={TITLE} — Details",
                        "--width=480",
                        "--ok-label=Close",
                        "--no-markup",
                        f"--text={message.details}",
                    ],
                    stop,
                    env,
                )
            if _dismissed(result, helper):
                return True
        except OSError:
            pass
    if stop is not None and stop.is_set():
        return False
    if not runtime:
        _notify_error(TITLE, f"{message.heading}\n\n{message.details}", env)
    return False
