import os
import signal
import subprocess
import sys
from unittest.mock import Mock

import pytest

from claude_geo_guard.platforms.linux import processes as pm


def proc_entry(root, pid, parent, executable, start=100, name="Claude (test)"):
    directory = root / str(pid)
    directory.mkdir()
    # stat fields after comm: state, ppid, ... starttime at index 19.
    fields = ["S", str(parent)] + ["0"] * 17 + [str(start)]
    (directory / "stat").write_text(f"{pid} ({name}) " + " ".join(fields))
    (directory / "exe").symlink_to(executable)


def test_exact_executable_and_ancestry(tmp_path):
    proc_entry(tmp_path, 100, 1, "/opt/Claude")
    proc_entry(tmp_path, 101, 100, "/bin/child")
    proc_entry(tmp_path, 102, 101, "/bin/grandchild")
    proc_entry(tmp_path, 103, 1, "/unrelated/claude", name="claude")
    result = pm.scan({"/opt/Claude"}, proc=tmp_path)
    assert {p.pid for p in result} == {100, 101, 102}


def test_reparented_child_and_reused_pid(tmp_path):
    proc_entry(tmp_path, 101, 1, "/bin/child", start=100)
    previous = [pm.Process(101, 100, 50, "/bin/child")]
    assert len(pm.scan(set(), previous, proc=tmp_path)) == 1
    previous = [pm.Process(101, 99, 50, "/bin/child")]
    assert not pm.scan(set(), previous, proc=tmp_path)


def test_other_owner_ignored(tmp_path, monkeypatch):
    proc_entry(tmp_path, 100, 1, "/opt/Claude")
    monkeypatch.setattr(os, "getuid", lambda: -1)
    assert not pm.scan({"/opt/Claude"}, proc=tmp_path)


def test_exited_process(tmp_path):
    (tmp_path / "100").mkdir()
    assert pm.inspect(100, tmp_path) is None
    assert pm.scan({"/opt/Claude"}, proc=tmp_path) == []


def test_pidfd_revalidation(monkeypatch):
    process = pm.Process(123, 10, 1, "/opt/Claude")
    monkeypatch.setattr(os, "pidfd_open", lambda _: 999)
    close = Mock()
    send = Mock()
    monkeypatch.setattr(os, "close", close)
    monkeypatch.setattr(signal, "pidfd_send_signal", send)
    monkeypatch.setattr(pm, "inspect", lambda _: pm.Process(123, 11, 1, "/unrelated"))
    assert not pm.send(process, signal.SIGKILL)
    send.assert_not_called()
    close.assert_called_once_with(999)


@pytest.mark.parametrize("ignore", [False, True])
def test_real_termination_and_force(ignore):
    program = "import signal,time; "
    if ignore:
        program += "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
    program += "print('ready',flush=True); time.sleep(60)"
    child = subprocess.Popen([sys.executable, "-c", program], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        identity = pm.inspect(child.pid)
        assert identity is not None
        pm.terminate([identity], grace=0.1)
        child.wait(timeout=3)
        assert child.returncode == -(signal.SIGKILL if ignore else signal.SIGTERM)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


def test_gone_before_signal(monkeypatch):
    monkeypatch.setattr(os, "pidfd_open", Mock(side_effect=ProcessLookupError()))
    assert not pm.send(pm.Process(123, 1, 1, "/opt/Claude"), signal.SIGTERM)


def test_idle_scan_skips_process_details(tmp_path, monkeypatch):
    proc_entry(tmp_path, 100, 1, "/other/app")
    inspect = Mock()
    monkeypatch.setattr(pm, "inspect", inspect)
    assert pm.scan({"/opt/Claude"}, proc=tmp_path) == []
    inspect.assert_not_called()
