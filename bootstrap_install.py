"""Install an exact release tag or the current master in a temporary checkout."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen

REPO = "mhbahmani/Claude-Geo-Guard"


def main(args):
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10+ required")
    if "--help" in args or "-h" in args:
        print("--dev selects master; the default is the latest published release.")
        return subprocess.call(
            [sys.executable, "-IB", str(Path(__file__).with_name("run.py")), "install", "--help"]
        )
    if "--dev" in args:
        args = [a for a in args if a != "--dev"]
        ref = "refs/heads/master"
    else:
        try:
            with urlopen(
                f"https://api.github.com/repos/{REPO}/releases/latest", timeout=15
            ) as response:
                tag = json.load(response)["tag_name"]
        except (OSError, ValueError, KeyError) as error:
            raise SystemExit(
                f"Cannot resolve latest GitHub release ({error}); use --dev for master"
            )
        ref = "refs/tags/" + tag
    with tempfile.TemporaryDirectory() as work:
        subprocess.run(["git", "init", "-q", work], check=True)
        subprocess.run(
            ["git", "-C", work, "fetch", "-q", "--depth=1", f"https://github.com/{REPO}.git", ref],
            check=True,
        )
        subprocess.run(["git", "-C", work, "checkout", "-q", "--detach", "FETCH_HEAD"], check=True)
        return subprocess.call(
            [sys.executable, "-IB", str(Path(work) / "run.py"), "install", *args]
        )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
