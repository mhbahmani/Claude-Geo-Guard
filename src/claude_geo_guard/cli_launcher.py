import os
import re
import subprocess
import sys
from pathlib import Path

from .checker import check
from .timezone import launch_environment

MIN_VERSION = (2, 1, 295)


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
    output = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, timeout=5, check=True
    ).stdout
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


def launch(paths, args, explicit=None):
    binary = discover(paths.bin, explicit)
    verdict = check()
    if not verdict.allowed:
        raise ValueError(verdict.reason)
    environment = launch_environment(paths)
    version(binary)
    print(
        "Claude Geo Guard: launch checks passed. Starting Claude Code.", file=sys.stderr, flush=True
    )
    os.execve(str(binary), [str(binary), *args], environment)
