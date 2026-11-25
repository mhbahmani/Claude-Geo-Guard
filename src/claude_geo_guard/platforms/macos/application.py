"""Build a local Finder applet and launch the independently signed Claude bundle."""

import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile

from ... import config
from ...checker import check
from ...launch_progress import verifying
from ...timezone import launch_environment
from . import dialogs, service

IDENTIFIER = "com.anthropic.claudefordesktop"
NAME = "Claude Guard.app"


def destination(paths):
    return paths.home / "Applications" / NAME


def identify(path):
    if not path.is_absolute() or path.suffix != ".app" or path.is_symlink():
        raise ValueError("select an absolute Claude.app bundle path")
    path = path.resolve(strict=True)
    info = plistlib.loads((path / "Contents/Info.plist").read_bytes())
    if not isinstance(info, dict):
        raise ValueError("invalid Claude Info.plist")
    name = info.get("CFBundleExecutable")
    if (
        info.get("CFBundleIdentifier") != IDENTIFIER
        or not isinstance(name, str)
        or Path(name).name != name
    ):
        raise ValueError("unexpected Claude bundle identity")
    exe = (path / "Contents/MacOS" / name).resolve(strict=True)
    if not exe.is_relative_to(path) or not exe.is_file() or not os.access(exe, os.X_OK):
        raise ValueError("invalid Claude executable")
    subprocess.run(
        [
            "/usr/bin/codesign",
            "--verify",
            "--strict",
            "-R",
            f'=anchor apple generic and identifier "{IDENTIFIER}" and certificate leaf[subject.OU] = "Q6L2SF6YDW"',
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    signature = subprocess.run(
        ["/usr/bin/codesign", "-d", "-r-", str(path)], check=True, capture_output=True, timeout=5
    )
    if b"designated =>" not in signature.stdout + signature.stderr:
        raise ValueError("missing code signing identity")
    stat = exe.stat()
    return dict(
        path=str(path),
        executable=str(exe),
        identity=hashlib.sha256(signature.stdout + signature.stderr).hexdigest(),
        inode=stat.st_ino,
        modified=stat.st_mtime_ns,
    )


def discover(paths, explicit):
    choices = (
        [Path(explicit)]
        if explicit
        else [
            p
            for p in (paths.home / "Applications/Claude.app", Path("/Applications/Claude.app"))
            if p.is_dir()
        ]
    )
    if len(choices) != 1:
        raise ValueError("select one Claude.app with --desktop-app or use --skip-desktop")
    return identify(choices[0])


def build(paths, write):
    # osacompile ships with macOS. The applet handles ordinary launches and
    # explicitly declines document events. No provider data enters script source.
    guard = json.dumps(str(paths.bin / "claude-geo-guard"), ensure_ascii=False)
    script = f"""on launchGuard()
        do shell script (quoted form of {guard}) & " desktop"
    end launchGuard
    on run
        my launchGuard()
    end run
    on reopen
        my launchGuard()
    end reopen
    on open itemsToOpen
        display alert "Claude Guard does not open documents" as warning
    end open"""
    with tempfile.TemporaryDirectory() as work:
        app = Path(work) / NAME
        subprocess.run(
            ["/usr/bin/osacompile", "-o", str(app), "-"],
            input=script,
            text=True,
            check=True,
            timeout=30,
        )
        plist = app / "Contents/Info.plist"
        info = plistlib.loads(plist.read_bytes())
        info.update(
            CFBundleIdentifier="local.claude-geo-guard.launcher", CFBundleName="Claude Guard"
        )
        plist.write_bytes(plistlib.dumps(info))
        subprocess.run(
            ["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True, timeout=30
        )
        for file in sorted(app.rglob("*")):
            if file.is_symlink():
                raise ValueError("unexpected symlink in compiled launcher")
            if file.is_file():
                write(
                    destination(paths) / file.relative_to(app),
                    file.read_bytes(),
                    file.stat().st_mode & 0o777,
                )


def launch(paths, args):
    if args:
        raise ValueError("Claude Guard only supports opening the application")
    record = json.loads(paths.manifest.read_text()).get("mac_app")
    if not record:
        raise ValueError("install macOS Desktop protection first")
    if not config.load(paths)["enabled"]:
        print(config.DISABLED_MESSAGE, file=sys.stderr, flush=True)
        dialogs.present(
            'display alert "Claude Guard" message (item 1 of argv) as warning',
            config.DISABLED_MESSAGE,
        )
        subprocess.run(["/usr/bin/open", "-a", record["path"]], check=True, timeout=15)
        return
    with verifying():
        if not service.healthy(paths):
            raise ValueError("runtime monitor is inactive")
        verdict = check()
        detail = "\n".join(
            f"IPv{family}: {verdict.countries.get(family, 'unavailable')} · {verdict.ips.get(family, 'unavailable')}"
            for family in sorted(verdict.countries.keys() | verdict.ips.keys())
        )
        if not verdict.allowed:
            raise ValueError(
                f"{verdict.reason}\nAllowed country: {verdict.allowed_country}\n{detail or 'Country and IP unavailable'}"
            )
        if identify(Path(record["path"])) != record:
            raise ValueError("Claude.app changed; inspect it and rerun installation")
        if not service.healthy(paths):
            raise ValueError("runtime monitor stopped during verification")
        env = launch_environment(paths)
    notify_success = config.load(paths)["success_notifications"]
    flags = ["--env", "TZ=" + env["TZ"]] if "TZ" in env else []
    subprocess.run(["/usr/bin/open", *flags, "-a", record["path"]], check=True, timeout=15, env=env)
    if notify_success:
        dialogs.notify(detail)
