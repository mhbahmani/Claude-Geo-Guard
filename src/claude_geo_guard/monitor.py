import json
import logging
import signal
import threading

from . import config
from .checker import Verdict, check
from .desktop_launcher import LaunchBlocked
from .platforms import backend
from .platforms import scan, terminate
from .storage import lock

LOG = logging.getLogger("claude-geo-guard")


class RuntimeAlerts:
    """One outstanding window; monitoring must never wait for its dismissal."""

    def __init__(self, stop=None):
        self.stop = stop if stop is not None else threading.Event()
        self.lock = threading.Lock()
        self.worker = None

    def __call__(self, verdict):
        if self.stop.is_set():
            return
        if not self.lock.acquire(blocking=False):
            LOG.info(json.dumps({"event": "runtime_dialog_already_open", "reason": verdict.reason}))
            return
        self.worker = threading.Thread(target=self._show, args=(verdict,), daemon=True)
        try:
            self.worker.start()
        except RuntimeError:
            self.worker = None
            self.lock.release()
            LOG.error(
                json.dumps({"event": "runtime_dialog_unavailable", "reason": "worker unavailable"})
            )

    def _show(self, verdict):
        try:
            error = LaunchBlocked(verdict.reason, kind="verification", verdict=verdict)
            shown = backend("dialogs").show_error(error, runtime=True, stop=self.stop)
            if not shown and not self.stop.is_set():
                LOG.warning(
                    json.dumps(
                        {
                            "event": "runtime_dialog_unavailable",
                            "reason": "check zenity/kdialog and the systemd user graphical environment; notification fallback attempted",
                        }
                    )
                )
        except Exception as error:
            # A presentation failure must not interrupt runtime protection.
            LOG.error(
                json.dumps({"event": "runtime_dialog_failed", "error_type": type(error).__name__})
            )
        finally:
            self.lock.release()

    def close(self):
        self.stop.set()
        if self.worker is not None:
            self.worker.join(timeout=4)


notify = RuntimeAlerts()


def tick(
    binaries,
    previous=(),
    grace=5,
    stop=None,
    checker=check,
    scanner=scan,
    killer=terminate,
    notifier=notify,
):
    processes = scanner(binaries, previous)
    if not processes:
        return []
    verdict = checker()
    LOG.info(
        json.dumps(
            {
                "event": "runtime_check",
                "allowed": verdict.allowed,
                "allowed_country": verdict.allowed_country,
                "reason": verdict.reason,
                "countries": verdict.countries,
                "provider": "ifconfig.io",
                "process_count": len(processes),
            }
        )
    )
    if not verdict.allowed:
        # Refresh ancestry after network I/O; retain already identified children.
        processes = scanner(binaries, processes)
        if not processes:
            return []
        LOG.warning(
            json.dumps(
                {
                    "event": "runtime_stop_requested",
                    "reason": verdict.reason,
                    "allowed_country": verdict.allowed_country,
                    "countries": verdict.countries,
                    "process_count": len(processes),
                    "grace_seconds": grace,
                }
            )
        )
        killer(processes, grace=grace, stop=stop)
        LOG.warning(json.dumps({"event": "runtime_stop_finished", "process_count": len(processes)}))
        notifier(verdict)
        return processes
    return processes


def run(paths, interval=None, grace=None):
    backend("processes")  # Reject unavailable runtime protection before acquiring a lock.
    for key, value in (("interval", interval), ("grace", grace)):
        if value is not None:
            config.validate_timing(key, value)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    alerts = RuntimeAlerts(stop)

    try:
        with lock(paths.manifest.parent / "monitor.lock"):
            previous = []
            while not stop.is_set():
                manifest = json.loads(paths.manifest.read_text())
                checker = check
                try:
                    preferences = config.load(paths)
                    cycle_interval = preferences["interval"] if interval is None else interval
                    cycle_grace = preferences["grace"] if grace is None else grace
                except (ValueError, OSError):
                    # A malformed edit must not stop the monitor and leave apps running.
                    cycle_interval, cycle_grace = 5, 0
                    reason = "guard configuration is invalid or unreadable; repair config.json"
                    LOG.error(json.dumps({"event": "configuration_invalid", "reason": reason}))

                    def checker():
                        return Verdict(False, reason, {})

                previous = tick(
                    set(manifest["binaries"]),
                    previous,
                    cycle_grace,
                    stop,
                    notifier=alerts,
                    checker=checker,
                )
                stop.wait(cycle_interval)
    finally:
        alerts.close()
