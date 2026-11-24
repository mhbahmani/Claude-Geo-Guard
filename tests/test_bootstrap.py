import importlib.util
import io
import subprocess
from pathlib import Path


def test_installs_published_tag_or_master(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("bootstrap", root / "bootstrap_install.py")
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True)
    result = tmp_path / "result"
    for value in ("release", "dev"):
        (repo / "run.py").write_text(
            f"import pathlib,sys\nassert sys.argv[1:] == ['install','--yes']\n"
            f"pathlib.Path({str(result)!r}).write_text({value!r})\n"
        )
        subprocess.run(["git", "-C", str(repo), "add", "run.py"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.org",
                "commit",
                "-qm",
                value,
            ],
            check=True,
        )
        if value == "release":
            subprocess.run(["git", "-C", str(repo), "tag", "v1"], check=True)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{repo.as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", f"https://github.com/{bootstrap.REPO}.git")
    monkeypatch.setattr(bootstrap, "urlopen", lambda *a, **kw: io.BytesIO(b'{"tag_name":"v1"}'))
    assert bootstrap.main(["--yes"]) == 0 and result.read_text() == "release"
    assert bootstrap.main(["--yes", "--dev"]) == 0 and result.read_text() == "dev"
