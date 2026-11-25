#!/bin/sh
# Standalone bootstrap for both ./install.sh and curl ... | sh.
set -eu
install_guard() {
    command -v python3 >/dev/null || { echo "Python 3.10+ is required" >&2; return 2; }
    case "$0" in
        */install.sh|install.sh)
            case "${1:-}" in
                --check|--status)
                    cmd=${1#--}; shift
                    root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
                    if [ -f "$root/run.py" ]; then
                        exec python3 -IB "$root/run.py" "$cmd" "$@"
                    fi
                    set -- "--$cmd" "$@"
                    ;;
            esac
            ;;
    esac
    bootstrap=$(cat <<'PY'
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import quote

if sys.version_info < (3, 10):
    sys.exit("Python 3.10+ is required")

repo = "mhbahmani/Claude-Geo-Guard"
args = sys.argv[1:]
dev = "--dev" in args
args = [arg for arg in args if arg != "--dev"]
command = "install"
if args[:1] in (["--check"], ["--status"]):
    command = args.pop(0)[2:]
if "--help" in args or "-h" in args:
    print("Source: latest published release by default; --dev selects master.", flush=True)

curl = ["curl", "-q", "--fail", "--show-error", "--location",
        "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "10", "--max-time", "90"]

def download(url, archive, version):
    label = f"Downloading Claude Geo Guard {version} from GitHub"
    command = [*curl, "--silent", "--max-filesize", "16777216", "--output", str(archive), url]
    if not sys.stderr.isatty():
        print(label + "…", file=sys.stderr, flush=True)
        subprocess.run(command, check=True)
        return

    print(label, end="", file=sys.stderr, flush=True)
    try:
        with subprocess.Popen(command) as transfer:
            dots = 0
            while True:
                try:
                    if transfer.wait(timeout=0.3):
                        raise subprocess.CalledProcessError(transfer.returncode, command)
                    break
                except subprocess.TimeoutExpired:
                    if dots == 3:
                        print("\b \b" * 3, end="", file=sys.stderr, flush=True)
                        dots = 0
                    else:
                        print(".", end="", file=sys.stderr, flush=True)
                        dots += 1
    finally:
        print(file=sys.stderr, flush=True)

def install(work):
    if dev:
        version = "master"
        ref = "heads/master"
    else:
        print("Looking up the latest Claude Geo Guard release…", file=sys.stderr, flush=True)
        result = subprocess.check_output([*curl, "--silent", "--max-filesize", "1048576",
            f"https://api.github.com/repos/{repo}/releases/latest"])
        version = json.loads(result)["tag_name"]
        ref = "tags/" + quote(version, safe="")
    archive = work / "source.tar.gz"
    download(f"https://codeload.github.com/{repo}/tar.gz/refs/{ref}", archive, version)
    print("Download complete. Unpacking the source archive…", file=sys.stderr, flush=True)
    # Only copy the Python entry point and package. Never extract links or special files.
    seen = set()
    total = 0
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            parts = member.name.split("/")[1:]
            if not (parts == ["run.py"] or parts[:2] == ["src", "claude_geo_guard"]):
                continue
            if not member.name.endswith(".py"):
                continue
            if not member.isreg() or any(part in ("", ".", "..") for part in parts):
                raise ValueError("invalid source archive path")
            target = work.joinpath(*parts)
            total += member.size
            if target in seen or total > 16777216:
                raise ValueError("invalid source archive contents")
            seen.add(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.extractfile(member).read())
    if work / "run.py" not in seen or work / "src/claude_geo_guard/cli.py" not in seen:
        raise ValueError("incomplete source archive")
    return subprocess.call([sys.executable, "-IB", str(work / "run.py"), command, *args])

try:
    with tempfile.TemporaryDirectory(prefix="claude-geo-guard-") as directory:
        sys.exit(install(Path(directory)))
except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.SubprocessError) as error:
    sys.exit(f"Claude Geo Guard installation failed: {error}")
PY
)
    if [ ! -t 0 ] && [ -t 1 ] && ( : </dev/tty ) 2>/dev/null; then
        exec python3 -IB -c "$bootstrap" "$@" </dev/tty
    fi
    exec python3 -IB -c "$bootstrap" "$@"
}
install_guard "$@"
