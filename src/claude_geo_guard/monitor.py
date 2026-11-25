import json
import logging
import signal
import threading

from . import config, executables, health
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
    paths=None,
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
                "provider": "Claude edge traces (plus ifconfig.io in full mode)",
                "process_count": len(processes),
            }
        )
    )
    if not verdict.allowed:
        # Refresh ancestry after network I/O; retain already identified children.
        processes = scanner(binaries, processes)
        if not processes:
            return []
        # A disable during a slow network probe takes effect before termination.
        if paths is not None:
            try:
                if not config.load(paths)["enabled"]:
                    return []
            except (OSError, ValueError):
                pass  # An unreadable configuration still fails closed.
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
        # The alert worker runs independently of termination and its grace period.
        notifier(verdict)
        killer(processes, grace=grace, stop=stop)
        LOG.warning(json.dumps({"event": "runtime_stop_finished", "process_count": len(processes)}))
        return processes
    return processes


class Coverage:
    """Executables to protect. A failed reload keeps the last validated manifest."""

    def __init__(self, paths):
        self.paths = paths
        self.signature = ()
        self.manifest = None
        self.error = None
        self.binaries = set()
        self.reported = set()

    def _report(self, event, **details):
        key = (event, *details.values())
        if key not in self.reported:
            self.reported.add(key)
            LOG.warning(json.dumps({"event": event, **details}))

    def refresh(self):
        try:
            info = self.paths.manifest.stat()
            signature = (info.st_ino, info.st_mtime_ns, info.st_size)
        except OSError:
            signature = None
        if signature != self.signature:
            self.signature = signature
            try:
                self.manifest = executables.load_manifest(self.paths)
                self.error = None
                self.binaries = set()
            except (OSError, ValueError) as error:
                self.error = f"installation manifest unreadable: {error}"
        if self.manifest is not None:
            known = executables.validated(self.manifest)
            for binary in set(self.manifest["binaries"]) - known:
                self._report("registration_ignored", executable=binary)
            accepted, unrecognized = executables.resolve_launchers(
                executables.manifest_launchers(self.manifest), known
            )
            for target in accepted - known - self.binaries:
                self._report("executable_update_accepted", executable=target)
            for launcher, target in unrecognized.items():
                self._report("executable_unrecognized", launcher=launcher, target=target)
            # Previous update targets stay registered for processes that are still running.
            self.binaries |= known | accepted
        return self.error


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
            coverage = Coverage(paths)
            previous, state = [], ()
            while not stop.is_set():
                manifest_error = coverage.refresh()
                checker, config_error, enabled = check, None, True
                try:
                    preferences = config.load(paths)
                    cycle_interval = preferences["interval"] if interval is None else interval
                    cycle_grace = preferences["grace"] if grace is None else grace
                    enabled = preferences["enabled"]
                except (ValueError, OSError) as error:
                    # A malformed edit must not stop the monitor and leave apps running.
                    cycle_interval, cycle_grace = 5, 0
                    config_error = f"guard configuration is invalid or unreadable: {error}"

                    def checker(reason=config_error):
                        return Verdict(False, reason, {})

                problem = config_error or manifest_error
                if problem != state:
                    _transition(paths, problem, config_error is not None)
                    state = problem
                if not enabled:
                    previous = []
                    stop.wait(cycle_interval)
                    continue
                previous = tick(
                    set(coverage.binaries),
                    previous,
                    cycle_grace,
                    stop,
                    paths=paths,
                    notifier=alerts,
                    checker=checker,
                )
                stop.wait(cycle_interval)
    finally:
        health.withdraw(paths)
        alerts.close()


def _transition(paths, problem, configuration):
    if problem is None:
        LOG.info(json.dumps({"event": "monitor_ready"}))
    else:
        event = "configuration_invalid" if configuration else "monitor_degraded"
        LOG.error(json.dumps({"event": event, "reason": problem}))
    try:
        health.publish(paths, "ready" if problem is None else "degraded", problem)
    except OSError as error:
        LOG.error(json.dumps({"event": "health_unwritable", "error_type": type(error).__name__}))
