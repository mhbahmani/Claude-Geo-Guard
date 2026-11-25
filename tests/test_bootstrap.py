import io
import json
import os
import pty
import select
import threading
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest


@pytest.mark.parametrize("dev,terminal", [(True, False), (False, False), (True, True)])
def test_piped_install_from_empty_directory(tmp_path, monkeypatch, dev, terminal):
    script = (Path(__file__).resolve().parents[1] / "install.sh").read_bytes()
    work = tmp_path / "empty"
    work.mkdir()
    urls = tmp_path / "urls"
    monkeypatch.setenv("URLS", str(urls))
    animation_seen = tmp_path / "animation-seen"
    monkeypatch.setenv("ANIMATION_SEEN", str(animation_seen))
    archive = tmp_path / "archive"
    monkeypatch.setenv("ARCHIVE", str(archive))
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in {
            "run.py": b"import json,sys;print(json.dumps(sys.argv[1:]))",
            "src/claude_geo_guard/cli.py": b"",
        }.items():
            member = tarfile.TarInfo("source/" + name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    curl = tmp_path / "curl"
    curl.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import sys,os,shutil
if '--output' in sys.argv:
    assert '--silent' in sys.argv and '--progress-bar' not in sys.argv
    if sys.stderr.isatty():
        import time
        deadline = time.monotonic() + 15
        while not os.path.exists(os.environ['ANIMATION_SEEN']):
            if time.monotonic() >= deadline:
                sys.exit("test did not observe the download animation")
            time.sleep(0.01)
with open(os.environ['URLS'],'a') as out: print(sys.argv[-1],file=out)
if '--output' in sys.argv: shutil.copyfile(os.environ['ARCHIVE'],sys.argv[sys.argv.index('--output')+1])
else: print('{"tag_name":"v0.2.0"}')
"""
    )
    curl.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + ":" + os.environ["PATH"])
    args = ["--yes", "--country", "DE"]
    master, slave = pty.openpty()
    animation = b"GitHub..." + b"\b \b" * 3 + b"."
    chunks = bytearray()
    finished = threading.Event()

    def read_terminal():
        # Drain concurrently: PTY reads may split frames and pipe buffers are finite.
        while True:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                data = os.read(master, 8192)
                if not data:
                    return
                chunks.extend(data)
                if animation in chunks:
                    animation_seen.touch()
            elif finished.is_set():
                return

    reader = threading.Thread(target=read_terminal, daemon=True) if terminal else None
    if reader:
        reader.start()
    try:
        result = subprocess.run(
            ["sh", "-s", "--", *args, *(["--dev"] if dev else [])],
            cwd=work,
            input=script,
            stdout=subprocess.PIPE,
            stderr=slave if terminal else subprocess.PIPE,
            check=True,
            timeout=20,
        )
    finally:
        finished.set()
        if reader:
            reader.join(timeout=2)
        os.close(slave)
        os.close(master)
    output = bytes(chunks) if terminal else result.stderr
    assert json.loads(result.stdout) == ["install", *args]
    version = "master" if dev else "v0.2.0"
    if terminal:
        assert animation in output
    else:
        assert b"\b" not in output
    messages = output.decode().splitlines()
    if not dev:
        assert "Looking up the latest" in messages[0]
    assert f"Downloading Claude Geo Guard {version} from GitHub" in messages[-2]
    assert "Download complete" in messages[-1]
    requests = urls.read_text().splitlines()
    assert requests[-1].endswith("refs/heads/master" if dev else "refs/tags/v0.2.0")
    assert len(requests) == (1 if dev else 2)
    assert not list(work.iterdir())
