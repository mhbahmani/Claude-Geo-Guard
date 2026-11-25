"""A confirmed policy failure pauses Claude's network side until a check passes again."""

import json
import logging
import time

from .platforms import backend
from .storage import atomic_write, json_bytes, read

LOG = logging.getLogger("claude-geo-guard")
LIMIT_SECONDS = 600
# Scan for new processes and window connections this often, only while paused.
MAINTAIN_SECONDS = 2


def _log(event, level=logging.WARNING, **details):
    LOG.log(level, json.dumps({"event": event, **details}))


ESCALATED_MESSAGE = "Claude opened a connection from its window, so the window is paused too."


def paused_message(verdict, limit, escalated=False):
    target = verdict.allowed_country or "your allowed country"
    detected = sorted(set(verdict.countries.values()) - {verdict.allowed_country})
    cause = (
        f"the connection is outside {target} (detected {', '.join(detected)})"
        if detected
        else f"the connection could not be confirmed in {target}"
    )
    window = "The window is paused too" if escalated else "The window stays open"
    return (
        f"Claude is paused: {cause}. {window} and reconnects once a check confirms "
        f"{target}. Claude closes after {round(limit / 60)} minutes."
    )


class Quarantine:
    def __init__(self, state=None, announce=None, adapter=None, limit=LIMIT_SECONDS):
        self.adapter = adapter or backend("processes")
        self.state = state
        self.announce = announce or (lambda message: None)
        self.limit = limit
        self._reset()

    def _reset(self):
        self.since = None
        self.verdict = None
        self.binaries = frozenset()
        self.targets = []
        self.frozen = {}
        self.escalated = False

    def _clear(self):
        self._reset()
        if self.state is not None:
            self.state.unlink(missing_ok=True)

    @property
    def active(self):
        return self.since is not None

    def expired(self):
        return self.active and time.monotonic() - self.since >= self.limit

    def engage(self, processes, binaries, verdict, adopted=()):
        first = not self.active
        if first:
            self.since = time.monotonic()
        self.verdict, self.binaries = verdict, frozenset(binaries)
        self.targets = list(processes)
        self.frozen.update({(p.pid, p.start): p for p in adopted})
        self.maintain(rescan=False, announce=not first)
        if first:
            _log(
                "runtime_paused",
                reason=verdict.reason,
                allowed_country=verdict.allowed_country,
                countries=verdict.countries,
                process_count=len(self.targets),
                paused_count=len(self.frozen),
            )
            self.announce(paused_message(verdict, self.limit, self.escalated))

    def maintain(self, rescan=True, announce=True):
        """Freeze network-capable processes started since the last pass. A window process
        that holds a connection freezes the window as well."""
        if not self.active:
            return
        if rescan:
            self.targets = self.adapter.scan(self.binaries, self.targets)
            if not self.targets and not any(map(self._alive, self.frozen.values())):
                _log("runtime_pause_ended", logging.INFO, reason="Claude exited")
                self._clear()
                return
        keep = frozenset() if self.escalated else self.binaries
        self.frozen.update({(p.pid, p.start): p for p in self.adapter.pause(self.targets, keep)})
        if not self.escalated:
            running = [
                p for p in self.adapter.expand(self.targets) if (p.pid, p.start) not in self.frozen
            ]
            if self.adapter.connected(running):
                self.escalated = True
                _log("runtime_pause_escalated", reason="a window process opened a connection")
                if announce:
                    self.announce(ESCALATED_MESSAGE)
                self.frozen.update(
                    {(p.pid, p.start): p for p in self.adapter.pause(self.targets, frozenset())}
                )
        self._save()

    def _alive(self, process):
        current = self.adapter.inspect(process.pid)
        return current is not None and current.start == process.start

    def release(self, reason, announce=True):
        if not self.active:
            return
        self.adapter.resume(list(self.frozen.values()))
        _log(
            "runtime_resumed",
            logging.INFO,
            reason=reason,
            paused_seconds=round(time.monotonic() - self.since, 1),
        )
        if announce:
            target = (self.verdict and self.verdict.allowed_country) or "your allowed country"
            self.announce(f"Connection verified in {target}. Claude resumed.")
        self._clear()

    def close(self, notifier):
        """The pause limit passed without a passing check: stop everything, then confirm."""
        processes = self.adapter.scan(self.binaries, self.targets)
        processes += [p for p in self.frozen.values() if p not in processes]
        verdict = self.verdict
        reason = verdict.reason if verdict else "paused before the monitor restarted"
        _log(
            "runtime_stop_requested",
            reason=f"{reason} (unresolved after {round(self.limit / 60)} minutes paused)",
            allowed_country=verdict.allowed_country if verdict else None,
            countries=verdict.countries if verdict else {},
            process_count=len(processes),
            grace_seconds=0,
        )
        if verdict is not None:
            notifier(verdict)
        survivors = self.adapter.halt(processes)
        _log(
            "runtime_stop_finished",
            process_count=len(processes),
            verified=not survivors,
            survivors=[p.pid for p in survivors],
        )
        self._clear()

    def _save(self):
        if self.state is None:
            return
        identities = [[p.pid, p.start] for p in self.frozen.values()]
        try:
            atomic_write(self.state, json_bytes({"frozen": identities}))
        except OSError as error:
            _log("pause_state_unwritable", error_type=type(error).__name__)

    def recover(self, binaries):
        """Take over processes a crashed monitor left frozen; the next check decides."""
        try:
            identities = json.loads(read(self.state) or b"{}").get("frozen", [])
            frozen = [
                p
                for pid, start in identities
                if (p := self.adapter.inspect(pid)) is not None and p.start == start
            ]
        except (OSError, ValueError, TypeError, AttributeError):
            frozen = []
        if not frozen:
            self._clear()
            return
        self.since = time.monotonic()
        self.binaries = frozenset(binaries)
        self.targets = list(frozen)
        self.frozen = {(p.pid, p.start): p for p in frozen}
        _log("runtime_pause_recovered", paused_count=len(frozen))
