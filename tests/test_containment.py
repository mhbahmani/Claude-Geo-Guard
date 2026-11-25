"""Pause and shutdown containment against a disposable fake Desktop; never the real Claude."""

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from unittest.mock import Mock

import pytest

from claude_geo_guard import monitor
from claude_geo_guard.checker import Verdict
from claude_geo_guard.platforms.linux import containment, processes
from claude_geo_guard.quarantine import ESCALATED_MESSAGE, Quarantine

FAKE = Path(__file__).with_name("fake_desktop.py")
SYSTEMD_RUN = shutil.which("systemd-run")
# Read before the isolation fixture replaces it; the user manager's bus lives there.
BUS = {"XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")}
MISMATCH = Verdict(False, "egress outside configured country GB", {4: "FR"}, "GB")
ALLOWED = Verdict(True, "verified", {4: "GB"}, "GB")


def _scopes_available():
    if not SYSTEMD_RUN or not Path("/sys/fs/cgroup/cgroup.controllers").exists():
        return False
    probe = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True)
    return probe.returncode == 0


SCOPES = _scopes_available()
MODES = [
    pytest.param(True, marks=pytest.mark.skipif(not SCOPES, reason="no systemd user scopes")),
    False,
]


def state(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]


class FakeDesktop:
    def __init__(self, root, scoped):
        python = os.path.realpath(sys.executable)
        self.desktop = root / "claude-desktop" / "claude-desktop"
        code = root / "claude-code" / "claude"
        crashpad = root / "claude-desktop" / "chrome_crashpad_handler"
        for target in (self.desktop, code, crashpad):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(python, target)
        self.binaries = frozenset({str(self.desktop)})
        self.state = root / "state"
        self.state.mkdir()
        self.receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.receiver.bind(("127.0.0.1", 0))
        self.receiver.setblocking(False)
        environ = dict(
            os.environ,
            FAKE_PORT=str(self.receiver.getsockname()[1]),
            FAKE_STATE=str(self.state),
            FAKE_CODE=str(code),
            FAKE_CRASHPAD=str(crashpad),
        )
        command = [str(self.desktop), "-I", str(FAKE)]
        self.unit = None
        if scoped:
            self.unit = f"{containment.PREFIX}test{uuid.uuid4().hex[:8]}"
            command = [SYSTEMD_RUN, "--user", "--scope", "--quiet", "--collect"]
            command += [f"--unit={self.unit}", "--", str(self.desktop), "-I", str(FAKE)]
            environ.update(BUS)
        self.main = subprocess.Popen(command, env=environ)
        seen, deadline = Counter(), time.monotonic() + 15
        while not ({"network", "code", "crashpad"} <= set(seen) and self.heartbeat()):
            assert time.monotonic() < deadline, f"fake desktop did not start: {seen}"
            time.sleep(0.05)
            seen += self.drain()

    def drain(self):
        counts = Counter()
        while True:
            try:
                counts[self.receiver.recv(64).decode()] += 1
            except BlockingIOError:
                return counts

    def quiet_period(self, seconds=0.5):
        """Datagrams sent over a fresh interval, after discarding anything in flight."""
        time.sleep(0.15)
        self.drain()
        time.sleep(seconds)
        return self.drain()

    def heartbeat(self):
        try:
            return int((self.state / "heartbeat").read_text() or 0)
        except (OSError, ValueError):
            return 0

    def crashpad(self):
        pid = int((self.state / "crashpad.pid").read_text())
        return processes.inspect(pid)

    def processes(self):
        return processes.scan(self.binaries)

    def close(self):
        targets = processes.scan(self.binaries)
        for process in targets:
            os.kill(process.pid, signal.SIGKILL)
        try:
            os.kill(int((self.state / "crashpad.pid").read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass
        if self.unit:
            subprocess.run(
                ["systemctl", "--user", "kill", "--signal=SIGKILL", f"{self.unit}.scope"],
                capture_output=True,
                env={**os.environ, **BUS},
            )
        self.main.wait(timeout=5)
        self.receiver.close()


@pytest.fixture
def control():
    process = subprocess.Popen(["/bin/sleep", "60"])
    yield process
    assert process.poll() is None, "an unrelated process was affected"
    process.kill()
    process.wait()


@pytest.fixture
def fake(request, tmp_path):
    app = FakeDesktop(tmp_path, request.param)
    yield app
    app.close()


@pytest.mark.parametrize("fake", MODES, indirect=True, ids=["scope", "unscoped"])
def test_pause_freezes_network_side_and_keeps_window_running(fake, control):
    frozen = processes.pause(fake.processes(), fake.binaries)
    beat = fake.heartbeat()
    counts = fake.quiet_period()
    assert counts["network"] == counts["code"] == 0
    assert fake.heartbeat() > beat, "the renderer stopped, so the window would freeze"
    assert state(fake.main.pid) != "T"
    if fake.unit:
        assert counts["crashpad"] == 0, "the scope must cover detached helpers"
    else:
        # Without a scope, a helper that left the tree cannot be found.
        assert counts["crashpad"] > 0
    processes.resume(frozen)
    assert fake.quiet_period()["network"] > 0


@pytest.mark.parametrize("fake", MODES, indirect=True, ids=["scope", "unscoped"])
def test_halt_sends_nothing_more_and_confirms_exit(fake, control):
    targets = fake.processes()
    scope = containment.scope(fake.main.pid)
    assert (scope is not None) == bool(fake.unit)
    fake.drain()
    survivors = processes.halt(targets)
    assert survivors == []
    assert fake.main.wait(timeout=5) == -signal.SIGKILL
    after = fake.quiet_period()
    assert "orphan" not in after, "SIGTERM handling must not get a chance to spawn work"
    if fake.unit:
        assert sum(after.values()) == 0
        assert fake.crashpad() is None
        assert containment.members(scope) == []
    else:
        assert set(after) == {"crashpad"}
        assert fake.crashpad() is not None


@pytest.mark.parametrize("fake", [False], indirect=True, ids=["unscoped"])
def test_graceful_terminate_leaks_traffic_and_spawned_children(fake, control):
    """The previous shutdown: requests continue during grace and new children survive."""
    targets = fake.processes()
    fake.drain()
    processes.terminate(targets, grace=0.3)
    counts = fake.quiet_period(0.3)
    assert counts["orphan"] > 0


@pytest.mark.skipif(not SCOPES, reason="no systemd user scopes")
@pytest.mark.parametrize("fake", [True], indirect=True, ids=["scope"])
def test_window_connection_escalates_to_a_full_pause(fake, control, tmp_path, monkeypatch):
    monkeypatch.setattr(processes, "_public", lambda address: not address.endswith(":0000"))
    announce = Mock()
    held = Quarantine(tmp_path / "paused.json", announce, processes, limit=60)
    held.engage(fake.processes(), fake.binaries, MISMATCH)
    assert not held.escalated and state(fake.main.pid) != "T"
    (fake.state / "connect").touch()
    deadline = time.monotonic() + 3
    while not held.escalated and time.monotonic() < deadline:
        time.sleep(0.1)
        held.maintain()
    assert held.escalated and state(fake.main.pid) == "T"
    beat = fake.heartbeat()
    assert sum(fake.quiet_period().values()) == 0 and fake.heartbeat() == beat
    held.release("verified")
    assert fake.quiet_period()["network"] > 0
    assert not (tmp_path / "paused.json").exists()


@pytest.mark.skipif(not SCOPES, reason="no systemd user scopes")
@pytest.mark.parametrize("fake", [True], indirect=True, ids=["scope"])
def test_pause_maintenance_is_cheap(fake, control, tmp_path):
    held = Quarantine(tmp_path / "paused.json", None, processes, limit=60)
    held.engage(fake.processes(), fake.binaries, MISMATCH)
    started = time.perf_counter()
    for _ in range(10):
        held.maintain()
    # Runs every two seconds only while paused; generous bound for slow CI machines.
    assert (time.perf_counter() - started) / 10 < 0.1
    held.release("verified", announce=False)


class FakeAdapter:
    def __init__(self, alive=True):
        self.alive = alive
        self.app = processes.Process(10, 1, 1, "/opt/claude")
        self.helper = processes.Process(11, 2, 10, "/opt/node")
        self.paused, self.resumed, self.halted = [], [], []
        self.connections = []

    def scan(self, binaries, previous=()):
        return [self.app] if self.alive else []

    def expand(self, found):
        return [*found, self.helper] if found else []

    def pause(self, found, keep):
        stopped = [p for p in self.expand(found) if p.executable not in keep]
        self.paused.append(stopped)
        return stopped

    def connected(self, found):
        return [p for p in found if p in self.connections]

    def resume(self, found):
        self.resumed.append(list(found))

    def halt(self, found):
        self.halted.append(list(found))
        return []

    def inspect(self, pid):
        return {10: self.app, 11: self.helper}.get(pid) if self.alive else None


def test_mismatch_pauses_until_a_check_passes(tmp_path):
    adapter, announce = FakeAdapter(), Mock()
    held = Quarantine(tmp_path / "paused.json", announce, adapter)
    killer = Mock()
    verdicts = iter([MISMATCH, MISMATCH, ALLOWED])

    def check_once():
        return monitor.tick(
            {"/opt/claude"},
            checker=lambda: next(verdicts),
            scanner=adapter.scan,
            killer=killer,
            notifier=Mock(),
            quarantine=held,
        )

    check_once()
    assert held.active and adapter.paused[0] == [adapter.helper]
    assert "detected FR" in announce.call_args.args[0]
    check_once()
    assert announce.call_count == 1, "a continuing mismatch must not notify again"
    assert (tmp_path / "paused.json").exists()
    check_once()
    assert not held.active and adapter.resumed == [[adapter.helper]]
    assert announce.call_args.args[0] == "Connection verified in GB. Claude resumed."
    killer.assert_not_called()


def test_transient_failure_keeps_the_pause():
    adapter = FakeAdapter()
    held = Quarantine(None, None, adapter)
    held.engage([adapter.app], {"/opt/claude"}, MISMATCH)
    timeout = Verdict(False, "timed out", {}, "GB", errors={4: "x"}, transient=True)
    monitor.tick(
        {"/opt/claude"},
        checker=lambda: timeout,
        scanner=adapter.scan,
        connectivity=monitor.Connectivity(routes=lambda: Mock(fingerprint="a")),
        quarantine=held,
    )
    assert held.active and adapter.resumed == []


def test_window_connection_escalates(tmp_path):
    adapter, announce = FakeAdapter(), Mock()
    adapter.connections = [adapter.app]
    held = Quarantine(None, announce, adapter)
    held.engage([adapter.app], {"/opt/claude"}, MISMATCH)
    assert held.escalated and adapter.paused[-1] == [adapter.app, adapter.helper]
    announce.assert_called_once()
    assert "The window is paused too" in announce.call_args.args[0]
    adapter.connections = []
    held.release("verified", announce=False)
    held.engage([adapter.app], {"/opt/claude"}, MISMATCH)
    adapter.connections = [adapter.app]
    held.maintain()
    assert announce.call_args.args[0] == ESCALATED_MESSAGE


def test_unresolved_pause_closes_and_reports_verified_completion(caplog):
    adapter, notifier = FakeAdapter(), Mock()
    held = Quarantine(None, None, adapter, limit=0)
    held.engage([adapter.app], {"/opt/claude"}, MISMATCH)
    assert held.expired()
    caplog.set_level("INFO")
    held.close(notifier)
    notifier.assert_called_once_with(MISMATCH)
    assert adapter.halted == [[adapter.app, adapter.helper]]
    assert '"verified": true' in caplog.text and "unresolved after 0 minutes" in caplog.text
    assert not held.active


def test_exit_while_paused_ends_quietly():
    adapter = FakeAdapter()
    held = Quarantine(None, None, adapter)
    held.engage([adapter.app], {"/opt/claude"}, MISMATCH)
    adapter.alive = False
    held.maintain()
    assert not held.active


def test_crashed_monitor_pause_is_recovered(tmp_path):
    adapter = FakeAdapter()
    first = Quarantine(tmp_path / "paused.json", None, adapter)
    first.engage([adapter.app], {"/opt/claude"}, MISMATCH)
    second = Quarantine(tmp_path / "paused.json", None, adapter)
    second.recover({"/opt/claude"})
    assert second.active and set(second.frozen) == {(11, 2)}
    second.release("verified", announce=False)
    assert adapter.resumed == [[adapter.helper]]


def test_rest_keeps_the_pause_complete_and_stops_at_the_limit():
    adapter = FakeAdapter()
    held = Quarantine(None, None, adapter, limit=60)
    held.engage([adapter.app], {"/opt/claude"}, MISMATCH)
    wake = Mock(sleep=Mock(return_value=False), stop=Mock(is_set=Mock(return_value=False)))
    held.since -= 59.9
    started = len(adapter.paused)
    assert monitor._rest(wake, 30, held) is False
    assert len(adapter.paused) > started and held.expired()
    assert wake.sleep.call_args.args[0] <= monitor.MAINTAIN_SECONDS
