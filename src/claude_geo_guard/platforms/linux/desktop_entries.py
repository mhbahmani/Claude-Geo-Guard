"""Conservative FreeDesktop interception, retaining comments and unrelated metadata."""

import os
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path


def values(text):
    if "\r" in text:
        raise ValueError(
            "CRLF desktop entries are unsupported; preserve the original and convert to LF"
        )
    result = {}
    section = None
    for line in text.splitlines():
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            if section in result:
                raise ValueError("duplicate desktop section")
            result[section] = {}
        elif "=" in line and not line.startswith("#") and section:
            key, value = line.split("=", 1)
            if key in result[section]:
                raise ValueError(f"duplicate desktop key {key}")
            result[section][key] = value
    if result.get("Desktop Entry", {}).get("Type") != "Application":
        raise ValueError("not an Application desktop entry")
    return result


def executable(command):
    # General string escaping precedes Exec quoting. Reject unknown escapes rather than guess.
    command = command.replace("\\\\", "\\")
    args = shlex.split(command, posix=True)
    if not args or "%" in args[0] or any(c in args[0] for c in "\n\r"):
        raise ValueError("unsupported desktop executable")
    found = args[0] if os.path.isabs(args[0]) else shutil.which(args[0])
    if not found or not Path(found).is_file() or not os.access(found, os.X_OK):
        raise ValueError(f"missing desktop executable: {args[0]}")
    path = Path(found).resolve()
    if path.name in {
        "env",
        "sh",
        "dash",
        "bash",
        "zsh",
        "fish",
        "flatpak",
        "snap",
        "node",
        "nodejs",
        "electron",
        "perl",
        "ruby",
        "java",
    } or path.name.startswith("python"):
        raise ValueError("indirect desktop launcher cannot be safely process-monitored")
    with path.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            raise ValueError("desktop executable must be a native ELF binary, not a script")
    # Expansion remains the desktop environment's responsibility.
    index = 0
    while index < len(command):
        if command[index] == "%":
            if index + 1 == len(command) or command[index + 1] not in "%fFuUick":
                raise ValueError("unsupported desktop field code")
            index += 1
        index += 1
    return path


def quote(value):
    # Two escaping layers: Exec quoted argument and desktop string value.
    if any(c in str(value) for c in "\n\r\x00"):
        raise ValueError("unsafe desktop argument")
    value = str(value).replace("%", "%%")
    value = re.sub(r'([\\"`$])', r"\\\1", value)
    return '"' + value.replace("\\", "\\\\") + '"'


@dataclass
class Entry:
    id: str
    source: Path
    target: Path
    text: str
    autostart: bool = False


def discover(paths):
    roots = [
        paths.data,
        *map(Path, (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")),
    ]
    configs = [
        paths.config,
        *map(Path, (os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":")),
    ]
    entries = []
    for roots_, subdir, target_root, auto in (
        (roots, "applications", paths.data, False),
        (configs, "autostart", paths.config, True),
    ):
        seen = set()
        for root in roots_:
            if not root.is_absolute():
                continue
            local_ids = set()
            for file in sorted((root / subdir).rglob("*.desktop")):
                ident = str(file.relative_to(root / subdir)).replace("/", "-")
                if ident in local_ids:
                    raise ValueError(f"ambiguous desktop-file ID: {ident} in {root / subdir}")
                local_ids.add(ident)
                if ident in seen:
                    continue
                seen.add(ident)
                text = file.read_bytes().decode("utf-8")
                try:
                    main = values(text)["Desktop Entry"]
                except ValueError:
                    if "claude" in text.lower():
                        raise
                    continue
                if main.get("Hidden") == "true":
                    continue
                if (
                    re.search(r"\bclaude\b", main.get("Name", ""), re.I)
                    or "claude" in main.get("Exec", "").lower()
                ):
                    # Existing user entry may be nested: modify that exact entry to avoid ID collisions.
                    target = file if root == target_root else target_root / subdir / ident
                    entries.append(Entry(ident, file, target, text, auto))
    return entries


def intercept(entry, guard):
    parsed = values(entry.text)
    binaries = set()
    exec_sections = {
        name for name in parsed if name == "Desktop Entry" or name.startswith("Desktop Action ")
    }
    if "Exec" not in parsed["Desktop Entry"]:
        raise ValueError(f"{entry.id}: Desktop Entry has no interceptable Exec")
    main_executable = executable(parsed["Desktop Entry"]["Exec"])
    for section in exec_sections:
        if "Exec" not in parsed[section]:
            raise ValueError(f"{entry.id}: {section} has no interceptable Exec")
        candidate = executable(parsed[section]["Exec"])
        if candidate != main_executable:
            raise ValueError(
                f"{entry.id}: action executable differs from main application; cannot safely monitor"
            )
        binaries.add(str(candidate))
    output = []
    edits = []
    section = None
    for line in entry.text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        if bare.startswith("[") and bare.endswith("]"):
            section = bare[1:-1]
        replacement = bare
        if section in exec_sections and bare.startswith("Exec="):
            replacement = f"Exec={quote(guard)} desktop -- {bare[5:]}"
        elif section == "Desktop Entry" and bare.startswith("DBusActivatable="):
            replacement = "DBusActivatable=false"
        if replacement != bare:
            edits.append([section, bare, replacement])
        output.append(replacement + line[len(bare) :])
    return "".join(output), sorted(binaries), edits


def restore(current, edits):
    lines = current.splitlines(keepends=True)
    sections = []
    section = None
    for line in lines:
        bare = line.rstrip("\r\n")
        if bare.startswith("[") and bare.endswith("]"):
            section = bare[1:-1]
        sections.append(section)
    for section, before, after in edits:
        matches = [
            i
            for i, line in enumerate(lines)
            if sections[i] == section and line.rstrip("\r\n") == after
        ]
        if len(matches) != 1:
            raise ValueError("desktop launch line edited; manual reconciliation required")
        i = matches[0]
        lines[i] = before + lines[i][len(after) :]
    return "".join(lines)
