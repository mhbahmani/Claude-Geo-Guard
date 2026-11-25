# Complete runtime shutdown and network containment

## Handoff

Audit performed on Linux on 2026-11-25, against commit `3094773` (v0.11.0). The installed `monitor.py` and Linux `processes.py` matched the checkout. The runtime monitor was active and reporting successful country checks.

The user asked whether closing Claude Desktop actually stops every associated process and all requests to Claude servers. They preferred keeping their running Desktop session open. **No signals were sent to the user's Claude processes.** Read-only inspection and disposable localhost-only experiments were sufficient to demonstrate gaps. This document proposes follow-up work; it does not implement it.

The current implementation cannot guarantee complete process shutdown or immediate cessation of network traffic. Treat these as two separate requirements. Better process cleanup alone is not a network enforcement boundary.

## Decision (implemented)

- The country and IP check stays the main guard. Routing events only start a check sooner and never resume Claude on their own.
- A hard failure pauses rather than closes. Guarded Desktop launches run in a dedicated `claude-geo-guard-desktop-*` systemd user scope. On a hard failure, every network-capable member of the tree and scope is stopped (network service, embedded Claude Code, helpers, detached crash handler). The main, renderer and GPU processes keep the window usable. Only while paused, the monitor rescans every two seconds and pauses the window too if one of its processes holds a non-local connection.
- A passing check resumes Claude. After 10 unresolved minutes, the scope is frozen, killed through `cgroup.kill`, and its emptiness confirmed (`runtime_stop_finished` with `verified`). The tree outside a scope is stopped until it stops growing, then killed and confirmed.
- No privileged network enforcement was added. Freezing gives an unprivileged enforcement point: no further client egress after processes are stopped. The tests above showed the user's systemd and kernel support user-owned freeze and kill.
- Transient and offline failures never pause or close; the offline policy is unchanged.
- Tests use the disposable `tests/fake_desktop.py` tree, a localhost receiver and an unrelated control process (`tests/test_containment.py`). Pause maintenance costs are measured in a test. No real Claude process was signaled.
- Remaining gaps: services started through D-Bus or systemd activation live in other units; a Desktop started outside the guard has no scope (doctor reports it); terminal Claude Code sessions rely on hooks; macOS keeps graceful termination.

## Current behavior (before the decision)

- `processes.scan()` selects same-user processes whose executable matches a registered binary, previously observed PID/start-time identities, and their currently observable descendants.
- PID file descriptors and start-time checks protect signaling against PID reuse.
- On denial, the monitor refreshes its target list once, then calls `terminate()` with that fixed list.
- `terminate()` sends SIGTERM and SIGCONT, waits for the configured grace period, and sends SIGKILL to remaining recorded identities. The installed grace period during the audit was five seconds.
- It does not discover children created during shutdown or wait for confirmed exit after SIGKILL.
- The monitor emits `runtime_stop_finished` after the termination function returns. That event currently means the signaling routine finished, not that every associated process exited or every connection disappeared.

References: [Linux process discovery and signaling](../../src/claude_geo_guard/platforms/linux/processes.py), [monitor decision and shutdown logging](../../src/claude_geo_guard/monitor.py), [current security model](../security-model.md).

## Observed evidence

### Live process and socket inspection

The root Desktop PID was `1291701`. These IDs describe one historical snapshot and must not be used as targets in a later session.

| Process | Observation |
| --- | --- |
| Desktop network process, PID `1291909` | Selected by the scanner; owned established TCP connections and a UDP socket to `160.79.104.10:443`. |
| Embedded Claude Code, PID `1292570` | Selected through ancestry; also owned established connections to that address. |
| `/usr/lib/claude-desktop/chrome_crashpad_handler`, PID `1291834` | Reparented to the user service manager, PID `3100`, and omitted by a fresh scan. It had no internet socket in the inspected snapshot. |
| Helpers and tool descendants | The scanner included Desktop helper executables, shells, and processes started by an active Claude task. |

`ss -tunap` and `/proc` socket ownership were inspected. Socket endpoints alone do not identify encrypted HTTP requests or prove that every connection to an address belongs to a specific hostname. No request payloads or credentials were captured.

A fresh scan does not reproduce the monitor's in-memory history. The running monitor may retain a detached process if it saw that process before detachment. The crash helper finding therefore demonstrates unreliable discovery, especially after a monitor restart, rather than proof that the running monitor had definitely forgotten that PID.

Desktop processes occupied two user scopes:

- `app-com.anthropic.Claude-1291701.scope`
- `app-gnome-com.anthropic.Claude-1291701.scope`

The first scope also contained a nested GNOME session and service processes associated with work launched from Claude. Some had reparented to the user service manager and were absent from a fresh ancestry scan. **Do not assume either existing scope is a safe, dedicated kill boundary.** Establish ownership and intended lifecycle before terminating a whole scope.

### Disposable shutdown experiments

The experiments imported the installed Linux process module and invoked its actual `terminate()` function. Targets were explicitly captured disposable process identities, not the user's Claude processes. A UDP receiver listened only on `127.0.0.1`. Disposable survivors were killed afterward.

| Experiment | Result |
| --- | --- |
| A disposable process ignored SIGTERM and sent one localhost UDP datagram roughly every 10 ms. Call `terminate([identity], grace=0.3)`. | It sent **29 datagrams during shutdown** and exited from SIGKILL after approximately **0.308 seconds**. |
| A disposable parent handled SIGTERM by spawning a new sender with `start_new_session=True`, printing its PID, then exiting. Call `terminate()` with only the parent's recorded identity. | The parent exited normally. Its new child survived; the receiver counted **19 datagrams after `terminate()` returned**, over about 0.2 seconds. |

These counts are observations, not expected constants for tests. The assertions should be that traffic continues during grace, and that an untracked child can survive and send after the function returns.

Temporary audit scripts were `/tmp/guard-process-audit/inspect.py` and `/tmp/guard-process-audit/probe.py`. They are not durable project artifacts. If still present, run with Python's `-I` option: the filename `inspect.py` otherwise shadows the standard library module. Recreate the experiments from the descriptions above if the files are gone.

## Proposed work

### 1. Define the guarantees and ownership boundary

Separate these outcomes:

1. Close the visible Desktop window.
2. Terminate all processes belonging to a guarded launch, including detached and newly created descendants.
3. Prevent new outbound traffic immediately after a denial decision.

The second and third require stronger mechanisms than the current executable/ancestry snapshot. A Unix process group is not sufficient containment: a descendant can create a new session or group, as the experiment did. Evaluate a dedicated Linux cgroup or transient systemd unit established **before launching the application**, with descendants inheriting membership. Investigate delegated permissions, process migration, multiple launches, helpers, and external service activation. Do not simply kill an existing desktop scope because its name contains Claude.

Decide how launches through unguarded paths are handled. Adoption of an already-running process tree cannot reliably reconstruct children that have already detached. Any remaining coverage gap must be visible in diagnostics and documented.

### 2. Make termination observable and verifiable

- Keep PID/start-time identity checks and pidfd signaling where applicable.
- Discover and terminate late children within the owned containment boundary. Repeated ancestry scans alone can reduce races but cannot guarantee discovery after reparenting.
- Wait for bounded, verified exit after escalation. Report surviving identities, permission failures, and timeouts rather than unconditional success.
- Distinguish `shutdown_requested`, verified completion, and incomplete shutdown in monitor events. Preserve compatibility with `last-stop`, or migrate its parser and tests alongside event changes.
- Test service interruption during shutdown. Ensure a monitor restart does not silently lose responsibility for live guarded processes.
- Preserve unrelated applications and independently launched CLI sessions. Explicitly decide whether task services started from Claude should share its lifetime.

### 3. Treat network containment as a separate design

The grace period permits requests while a process handles SIGTERM. SIGSTOP is not a complete substitute: it does not retract queued traffic, and the current shutdown code sends SIGCONT so processes can handle SIGTERM.

If immediate blocking is required, evaluate a network enforcement boundary tied to the owned launch, such as an appropriately privileged cgroup/network-namespace design. Establish the deny rule **before** graceful shutdown. Cover IPv4, IPv6, TCP, UDP/QUIC, existing connections, and local proxies or delegated request services. Define installation permissions and cleanup behavior explicitly; do not add broad firewall rules as an incidental process-cleanup fix.

Requests already transmitted cannot be recalled, and server-side work may continue after the client disconnects. Phrase guarantees in terms of preventing further client egress after a defined enforcement point, not stopping all processing on Claude's servers.

The current offline policy intentionally leaves running applications open on transient verification failure. Decide whether network blocking applies only to confirmed policy denial or also to transient failures; do not silently change that policy while implementing shutdown containment.

## Acceptance criteria

- Reinspect a real Desktop tree, including its network process, embedded Code, crash helper, tool descendants, and detached helpers. Account for every relevant process or explicitly report unsupported coverage.
- Disposable fixtures cover ordinary exit, ignored SIGTERM, a child spawned during SIGTERM handling, double-fork/reparenting, session creation, PID reuse, and monitor restart.
- No owned process survives a reported successful shutdown. A deliberately unkillable or inaccessible target produces an incomplete result with evidence.
- Independent control processes survive every test. Do not use name matching or a broad shared executable as a kill criterion.
- A local receiver measures traffic throughout grace and after shutdown. If network containment is implemented, verify its defined enforcement point with TCP and UDP and both available address families, including established connections.
- Test application relaunch and supervisor restart behavior after denial. A new process must not silently bypass a boundary that is supposed to remain denied.
- Check that `last-stop`, error dialogs, and journal output distinguish a request from verified completion and show incomplete shutdown clearly.
- Run the existing process, monitor, offline-mode, installer, and integration tests, followed by `make check`.
- Only perform a real Desktop shutdown test when needed, after coordinating with the user and ensuring there is no unsaved work. The read-only audit and disposable reproductions above do not establish that an actual Desktop shutdown was tested.

## Suggested order

First add durable disposable reproductions for the two demonstrated gaps. Then implement verified process shutdown with an explicit ownership boundary and accurate completion reporting. Evaluate network containment separately against the desired guarantee. Finally validate real Desktop lifecycle behavior and update the security model and release notes with the exact achieved limits.
