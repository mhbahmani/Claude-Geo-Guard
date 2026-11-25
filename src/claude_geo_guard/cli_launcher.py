import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from . import config, settings_manager
from .checker import check
from .storage import read
from .launch_progress import terminal_progress
from .timezone import launch_environment

MIN_VERSION = (2, 1, 295)
VERSION_TIMEOUT_SECONDS = 30


def discover(wrapper_dir: Path, explicit=None, path=None):
    if explicit and not Path(explicit).expanduser().is_absolute():
        raise ValueError("explicit Claude executable must be an absolute path")
    candidates = (
        [Path(explicit).expanduser()]
        if explicit
        else [
            Path(p) / "claude"
            for p in (os.environ.get("PATH", "") if path is None else path).split(":")
            if p and Path(p).is_absolute()
        ]
    )
    for candidate in candidates:
        if candidate.parent.resolve() == wrapper_dir.resolve():
            continue
        if candidate.is_file() and os.access(candidate, os.X_OK):
            resolved = candidate.resolve()
            if resolved.parent == wrapper_dir.resolve():
                continue
            with resolved.open("rb") as stream:
                header = stream.read(4096)
                if b"CLAUDE_GEO_GUARD_WRAPPER" in header:
                    continue
            return candidate.absolute()
    raise ValueError("original Claude Code executable missing; set --claude-binary or fix PATH")


def version(binary):
    command = [str(binary), "--version"]
    try:
        output = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
            check=True,
        ).stdout
    except subprocess.TimeoutExpired as error:
        raise ValueError(
            f"Claude Code CLI version is unknown. The version check timed out after "
            f"{VERSION_TIMEOUT_SECONDS} seconds. Run {shlex.join(command)} in Terminal, "
            "then retry the guard once the command completes."
        ) from error
    match = re.fullmatch(r"\s*(\d+)\.(\d+)\.(\d+)(?:\s+\(Claude Code\))?\s*", output)
    if not match:
        raise ValueError(
            f"Cannot verify the Claude Code CLI version from {binary} --version "
            f"(stdout: {ascii(output.strip()[:160])}). Check the selected executable."
        )
    if tuple(map(int, match.groups())) < MIN_VERSION:
        required = ".".join(map(str, MIN_VERSION))
        raise ValueError(
            f"Detected Claude Code CLI {'.'.join(match.groups())} at {binary}. "
            f"Version {required} or later is required for fail-closed hooks. Update that CLI."
        )
    return output.strip()


# Options whose value is the next argument even when it starts with a dash.
VALUE_OPTIONS = {
    "--advisor",
    "--agent",
    "--agents",
    "--append-subagent-system-prompt",
    "--append-subagent-system-prompt-file",
    "--append-system-prompt",
    "--append-system-prompt-file",
    "--autocompact",
    "--correlation-id",
    "--debug-file",
    "--effort",
    "--environment",
    "--exec",
    "--fallback-model",
    "--input-format",
    "--json-schema",
    "--max-budget-usd",
    "--max-turns",
    "--metadata",
    "--model",
    "--name",
    "-n",
    "--output-format",
    "--permission-mode",
    "--permission-prompt-tool",
    "--permission-prompts",
    "--plugin-dir",
    "--plugin-url",
    "--ref",
    "--remote-control-session-name-prefix",
    "--session-id",
    "--setting-sources",
    "--settings",
    "--system-prompt",
    "--system-prompt-file",
    "--system-prompt-snapshot",
    "--teammate-mode",
}
NO_SESSION = {
    "--version",
    "-v",
    "--help",
    "-h",
    "auth",
    "auto-mode",
    "config",
    "doctor",
    "install",
    "logs",
    "mcp",
    "plugin",
    "plugins",
    "purge",
    "setup-token",
    "update",
}
HOOKLESS_FLAGS = {
    "--bare": "bare mode",
    "--safe-mode": "safe mode",
    "--restricted": "restricted mode",
}
HOOKLESS_ENVIRONMENT = {
    "CLAUDE_CODE_SIMPLE": "bare mode",
    "CLAUDE_CODE_SAFE_MODE": "safe mode",
}
MANAGED_SETTINGS = Path("/etc/claude-code")
BYPASS = "or run claude-geo-guard disable to start Claude without the guard"


def options(args):
    """Yield (option, value) pairs before `--`, leaving prompt text and option values alone."""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            return
        if arg.startswith("--") and "=" in arg:
            yield tuple(arg.split("=", 1))
        elif arg in VALUE_OPTIONS:
            yield arg, args[index + 1] if index + 1 < len(args) else None
            index += 1
        elif arg.startswith("-") and arg != "-":
            yield arg, None
        index += 1


def _load_settings(value):
    text = value.strip()
    try:
        raw = text.encode() if text.startswith("{") else Path(value).expanduser().read_bytes()
        data = json.loads(raw)
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot read --settings {value!r}: {error}") from None
    if not isinstance(data, dict):
        raise ValueError(f"--settings {value!r} must contain a JSON object")
    return data


def _read_optional(path):
    try:
        raw = read(path)
        return json.loads(raw) if raw is not None else None
    except (OSError, ValueError):
        return None


def hook_coverage(paths, args, environ=None):
    """Raise ValueError if this invocation would start Claude without the guard's hooks."""
    environ = os.environ if environ is None else environ
    if args[:1] and args[0] in NO_SESSION:
        return
    for name, mode in HOOKLESS_ENVIRONMENT.items():
        if environ.get(name, "").strip().lower() not in {"", "0", "false", "no", "off"}:
            raise ValueError(
                f"{name} starts Claude Code in {mode}, which skips the guard's hooks. "
                f"Unset {name}, {BYPASS}."
            )
    sources, extra = None, []
    for option, value in options(args):
        if option in HOOKLESS_FLAGS:
            raise ValueError(
                f"{option} starts Claude Code in {HOOKLESS_FLAGS[option]}, which skips the "
                f"guard's hooks. Remove {option}, {BYPASS}."
            )
        if option == "--setting-sources":
            sources = {item.strip() for item in (value or "").split(",") if item.strip()}
        elif option == "--settings" and value is not None:
            extra.append((f"--settings {value}", _load_settings(value)))
    if sources is not None and "user" not in sources:
        raise ValueError(
            "--setting-sources without user skips ~/.claude/settings.json and the guard's "
            f"hooks. Add user to the list, {BYPASS}."
        )
    root = environ.get("CLAUDE_CONFIG_DIR")
    settings = Path(root) / "settings.json" if root else paths.settings
    guard = paths.bin / "claude-geo-guard"
    try:
        data = settings_manager.parse(read(settings))
        registered = settings_manager.merge(data, settings_manager.owned_groups(guard), guard)
    except ValueError as error:
        raise ValueError(f"guard hooks unavailable in {settings}: {error}; rerun install") from None
    if registered != data:
        raise ValueError(
            f"the guard's hooks are missing or edited in {settings}. "
            "Run ./install.sh again to restore them."
        )
    project = Path.cwd() / ".claude"
    candidates = [*extra]
    if sources is None or "project" in sources:
        candidates.append(
            (str(project / "settings.json"), _read_optional(project / "settings.json"))
        )
    if sources is None or "local" in sources:
        local = project / "settings.local.json"
        candidates.append((str(local), _read_optional(local)))
    for label, values in candidates:
        if isinstance(values, dict) and values.get("disableAllHooks") is True:
            raise ValueError(f"{label} sets disableAllHooks, which skips the guard's hooks")
    managed = [MANAGED_SETTINGS / "managed-settings.json"]
    managed += sorted((MANAGED_SETTINGS / "managed-settings.d").glob("*.json"))
    for path in managed:
        values = _read_optional(path)
        if isinstance(values, dict) and (
            values.get("disableAllHooks") is True or values.get("allowManagedHooksOnly") is True
        ):
            raise ValueError(f"managed settings in {path} turn off user hooks, including the guard")


def launch(paths, args, explicit=None):
    binary = discover(paths.bin, explicit)
    if not config.load(paths)["enabled"]:
        print(config.DISABLED_MESSAGE, file=sys.stderr, flush=True)
        return os.execve(str(binary), [str(binary), *args], os.environ)
    with terminal_progress():
        hook_coverage(paths, args)
        # The slow version probe runs first so the egress verdict is as fresh as possible.
        version(binary)
        verdict = check()
        if not verdict.allowed:
            raise ValueError(verdict.reason)
        environment = launch_environment(paths)
    print(
        "Claude Geo Guard: launch checks passed. Starting Claude Code.", file=sys.stderr, flush=True
    )
    os.execve(str(binary), [str(binary), *args], environment)
