import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest


@pytest.mark.parametrize("dev", [True, False])
def test_piped_install_from_empty_directory(tmp_path, monkeypatch, dev):
    script = (Path(__file__).resolve().parents[1] / "install.sh").read_bytes()
    work = tmp_path / "empty"
    work.mkdir()
    urls = tmp_path / "urls"
    monkeypatch.setenv("URLS", str(urls))
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
with open(os.environ['URLS'],'a') as out: print(sys.argv[-1],file=out)
if '--output' in sys.argv: shutil.copyfile(os.environ['ARCHIVE'],sys.argv[sys.argv.index('--output')+1])
else: print('{"tag_name":"v0.2.0"}')
"""
    )
    curl.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + ":" + os.environ["PATH"])
    args = ["--yes", "--country", "DE"]
    result = subprocess.run(
        ["sh", "-s", "--", *args, *(["--dev"] if dev else [])],
        cwd=work,
        input=script,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == ["install", *args]
    version = "master" if dev else "v0.2.0"
    messages = result.stderr.decode().splitlines()
    if not dev:
        assert "Looking up the latest" in messages[0]
    assert f"Downloading Claude Geo Guard {version} from GitHub" in messages[-2]
    assert "Download complete" in messages[-1]
    requests = urls.read_text().splitlines()
    assert requests[-1].endswith("refs/heads/master" if dev else "refs/tags/v0.2.0")
    assert len(requests) == (1 if dev else 2)
    assert not list(work.iterdir())
