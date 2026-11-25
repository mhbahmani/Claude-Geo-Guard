import json
import logging
import signal
import subprocess
import threading
import time

from . import config, executables, health, verdict_cache
from .checker import Verdict, check
from .desktop_launcher import LaunchBlocked
from .platforms import backend
from .platforms import Unroutable, pause, resume, scan, snapshot, terminate, watch
from .quarantine import LIMIT_SECONDS, MAINTAIN_SECONDS, Quarantine
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

OFFLINE_MESSAGE = (
    "The connection could not be verified (slow or unreachable network). Claude keeps "
    "running; only a confirmed country or IP mismatch stops it."
)
ONLINE_MESSAGE = "Connection verified again. Normal checks have resumed."
# Starting another check while Claude is paused must stop after this long.
PAUSE_SECONDS = 10


def no_route(verdict):
    """Denied before any request could leave: nothing can reach Anthropic either."""
    return verdict.transient and not verdict.errors and not verdict.observations


class Connectivity:
    """Tracks transient failures and routing changes without treating them as verified."""

    OFFLINE_AFTER = 3
    RECHECK_SECONDS = (1, 3)

    def __init__(self, routes=snapshot, announce=None):
        self.routes = routes
        self.announce = announce or (lambda message: None)
        self.failures = 0
        self.offline = False
        # Offline and restored notices are informational: nothing is blocked while offline.
        self.notices = True
        # Routing fingerprints that were verified, or tolerated while offline.
        self.known = set()

    def current(self):
        try:
            return self.routes().fingerprint
        except Unroutable:
            return "unroutable"
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            return None

    def tell(self, message):
        if self.notices:
            self.announce(message)

    def moved(self):
        return bool(self.known) and self.current() not in self.known

    def allowed(self, verdict):
        """Record a verified network; True when this ends offline mode."""
        was_offline = self.offline
        self.failures, self.offline = 0, False
        self.known = {verdict.fingerprint} if verdict.fingerprint else set()
        return was_offline

    def unreachable(self, conclusive=False):
        """Count a transient failure; True when this one starts offline mode."""
        self.failures += 1
        entered = not self.offline and (conclusive or self.failures >= self.OFFLINE_AFTER)
        self.offline = self.offline or entered
        if self.offline and (fingerprint := self.current()) is not None:
            self.known.add(fingerprint)
        return entered

    def delay(self, interval):
        if not self.offline and 0 < self.failures < self.OFFLINE_AFTER:
            return self.RECHECK_SECONDS[self.failures - 1]
        return interval


def _confirm_while_paused(verdict, checker, stop):
    started = time.monotonic()
    for delay in Connectivity.RECHECK_SECONDS:
        if (
            not verdict.transient
            or no_route(verdict)
            or time.monotonic() - started >= PAUSE_SECONDS
        ):
            break
        if stop.wait(delay) if stop is not None else time.sleep(delay):
            break
        verdict = checker()
    return verdict


def _log(event, level=logging.INFO, **details):
    LOG.log(level, json.dumps({"event": event, **details}))


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
    connectivity=None,
    check_due=True,
    pauser=pause,
    resumer=resume,
    quarantine=None,
    share=None,
):
    processes = scanner(binaries, previous)
    if not processes:
        return []
    paused = []
    moved = connectivity is not None and connectivity.moved()
    if not (check_due or moved):
        return processes
    if moved and share is not None:
        share(None)  # Hooks must not reuse a verdict from the previous network.
    if moved and not (quarantine is not None and quarantine.active):
        # Claude must not talk over an unchecked network while verification runs.
        paused = pauser(processes, binaries)
        _log("network_changed", logging.WARNING, paused_count=len(paused))
    try:
        return _decide(
            binaries,
            processes,
            paused,
            grace,
            stop,
            checker,
            scanner,
            killer,
            notifier,
            paths,
            connectivity,
            quarantine,
            share,
        )
    finally:
        resumer(paused)


def _decide(
    binaries,
    processes,
    paused,
    grace,
    stop,
    checker,
    scanner,
    killer,
    notifier,
    paths,
    connectivity,
    quarantine,
    share=None,
):
    verdict = checker()
    if paused:
        verdict = _confirm_while_paused(verdict, checker, stop)
    if share is not None:
        share(verdict)
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
    if verdict.allowed:
        if quarantine is not None:
            quarantine.release("verified")
        if connectivity is not None and connectivity.allowed(verdict):
            _log("connection_restored")
            connectivity.tell(ONLINE_MESSAGE)
        return processes
    if verdict.transient and connectivity is not None:
        conclusive = bool(paused) or no_route(verdict)
        if connectivity.unreachable(conclusive):
            _log("offline_mode", logging.WARNING, reason=verdict.reason)
            connectivity.tell(OFFLINE_MESSAGE)
        elif not connectivity.offline:
            _log("recheck_scheduled", failures=connectivity.failures, reason=verdict.reason)
        return processes
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
        if quarantine is not None:
            quarantine.engage(processes, binaries, verdict, adopted=paused)
            paused.clear()  # Now held by the quarantine, not resumed by this check.
            return processes
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


class Wake:
    """The stop flag plus a sleep that routing changes cut short."""

    def __init__(self):
        self.stop = threading.Event()
        self._event = threading.Event()
        self._changed = False

    def halt(self, *_):
        self.stop.set()
        self._event.set()

    def poke(self):
        self._changed = True
        self._event.set()

    def sleep(self, seconds):
        """Returns True when a network change, not the timeout, ended the sleep."""
        self._event.wait(seconds)
        self._event.clear()
        changed, self._changed = self._changed, False
        return changed and not self.stop.is_set()


class RouteWatch:
    """Keeps a route watcher running; a dead one is restarted at most once a minute."""

    RETRY_SECONDS = 60

    def __init__(self, callback):
        self.callback = callback
        self.watcher = None
        self.retry = 0.0

    def ensure(self):
        if self.watcher is not None and self.watcher.alive():
            return
        if time.monotonic() < self.retry:
            return
        self.retry = time.monotonic() + self.RETRY_SECONDS
        if self.watcher is not None:
            _log("route_watch_stopped", logging.WARNING)
            self.close()
        try:
            self.watcher = watch(self.callback)
        except (OSError, ValueError, RuntimeError) as error:
            _log("route_watch_unavailable", logging.WARNING, error_type=type(error).__name__)

    def close(self):
        if self.watcher is not None:
            self.watcher.close()
            self.watcher = None


def _announcer():
    def announce(message):
        def show():
            try:
                backend("dialogs").notify(message)
            except Exception as error:
                _log("notification_failed", logging.WARNING, error_type=type(error).__name__)

        threading.Thread(target=show, daemon=True).start()

    return announce


def _sharer(paths, interval):
    """Publishes each verdict to hooks until the next check is due."""

    def share(verdict):
        try:
            if verdict is None:
                verdict_cache.clear(paths)
            else:
                verdict_cache.store(paths, verdict, interval + verdict_cache.MONITOR_SLACK_SECONDS)
        except OSError as error:
            _log("verdict_unwritable", logging.WARNING, error_type=type(error).__name__)

    return share


def _rest(wake, seconds, quarantine):
    """Sleep until the next check; while paused, keep the pause complete in between."""
    if quarantine is None or not quarantine.active:
        return wake.sleep(seconds)
    end = time.monotonic() + seconds
    while True:
        if wake.sleep(min(max(0.0, end - time.monotonic()), MAINTAIN_SECONDS)):
            return True
        if wake.stop.is_set() or time.monotonic() >= end:
            return False
        quarantine.maintain()
        if quarantine.expired():
            return False


def run(paths, interval=None, grace=None, pause_limit=None):
    adapter = backend("processes")  # Reject unavailable runtime protection before a lock.
    for key, value in (("interval", interval), ("grace", grace)):
        if value is not None:
            config.validate_timing(key, value)
    wake = Wake()
    stop = wake.stop
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, wake.halt)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    alerts = RuntimeAlerts(stop)
    routes = RouteWatch(wake.poke)
    quarantine = None

    try:
        with lock(paths.manifest.parent / "monitor.lock"):
            coverage = Coverage(paths)
            announce = _announcer()
            connectivity = Connectivity(announce=announce)
            if hasattr(adapter, "halt"):
                quarantine = Quarantine(
                    paths.manifest.parent / "paused.json",
                    announce,
                    adapter,
                    LIMIT_SECONDS if pause_limit is None else pause_limit,
                )
                coverage.refresh()
                quarantine.recover(coverage.binaries)
            previous, state = [], ()
            network_event, deadline = False, 0.0
            while not stop.is_set():
                routes.ensure()
                manifest_error = coverage.refresh()
                checker, config_error, enabled = check, None, True
                try:
                    preferences = config.load(paths)
                    cycle_interval = preferences["interval"] if interval is None else interval
                    cycle_grace = preferences["grace"] if grace is None else grace
                    enabled = preferences["enabled"]
                    connectivity.notices = preferences["offline_notifications"]
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
                    if quarantine is not None:
                        quarantine.release("protection disabled", announce=False)
                    previous, network_event = [], False
                    wake.sleep(cycle_interval)
                    continue
                previous = tick(
                    set(coverage.binaries),
                    previous,
                    cycle_grace,
                    stop,
                    paths=paths,
                    notifier=alerts,
                    checker=checker,
                    connectivity=connectivity,
                    check_due=not network_event,
                    quarantine=quarantine,
                    share=_sharer(paths, cycle_interval),
                )
                if quarantine is not None and quarantine.expired():
                    quarantine.close(alerts)
                    previous = []
                if network_event:
                    # Routing events must not postpone the regular schedule.
                    pending = max(0.0, deadline - time.monotonic())
                else:
                    pending = connectivity.delay(cycle_interval)
                    deadline = time.monotonic() + pending
                network_event = _rest(wake, pending, quarantine)
    finally:
        if quarantine is not None:
            # A stopped monitor no longer protects anything; frozen apps must not stay stuck.
            quarantine.release("monitor stopped", announce=False)
        verdict_cache.clear(paths)
        routes.close()
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
