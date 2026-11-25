# Architecture

The system implements three independent checkpoints, sharing `checker.check()`:

1. Desktop and CLI wrappers verify egress, then replace themselves with the original executable.
2. A systemd user service identifies running Desktop processes and checks only while they exist.
3. Claude Code command hooks verify before prompt submission, prompt expansion, and tool execution.

The checker returns an explicit `Verdict`; an exception from network operations produces a denied result. It has injectable route/request functions for testing. It uses `ip -j` to snapshot all route tables, policy rules and interface addresses, and route lookup against representative IPv4/IPv6 public destinations for the current UID. Volatile lifetimes/cache fields are removed from comparisons. The actual HTTPS requests use curl `-4` and `-6`, normal TLS validation, no redirects, no proxy and no curl startup config. Both Claude trace endpoints are mandatory. Full mode additionally checks ifconfig.io. Each observation is validated independently for country, public IP and family; see [network checks](network-checks.md). Every configured allowed_ips match is exact and applies alongside country policy. Verified partial results remain available for display when another family fails. `config.py` validates the user-editable XDG config.json; `checker.py` reads the selected country and optional allowed_ips on every check (GB before first install). Missing installed config or any invalid setting denies verification. A policy change detected during verification denies the in-flight check. Curl supplies bounded connection/transfer times without implementing a second TLS client. A failed request that only timed out or lost its connection is retried once, for that destination and family alone, if the retry still fits a 20-second verification deadline. Confirmed mismatches and other failures are never retried, and the route and policy revalidation runs after the retry. The CLI wrapper also refuses invocations that would load Claude Code without the guard's hooks. All remaining runtime code uses the Python standard library.

## Modules

| Module | Responsibility |
|---|---|
| `checker`, `policy`, `linux/networking` | Geolocation policy, independent family checks, route changes |
| `cli_launcher`, `desktop_launcher`, `timezone` | Discovery, preflight checks, exact argv/exec and optional TZ environment |
| `settings_manager` | Strict JSON parsing, structural append/remove, ownership conflicts |
| `linux/desktop_entries` | XDG precedence, desktop IDs, metadata-preserving interception |
| `process_manager`, `monitor` | Exact executable/UID/ancestry evidence, pidfds, periodic policy |
| `linux/systemd`, `linux/shell` | User-unit generation and marked Bash/Zsh blocks |
| `storage`, `installer` | Atomic files, durable recovery journals, installation lifecycle |
| `cli`, `paths` | Commands, diagnostic distinctions, XDG layout |

## Timezone scope

The config.json `timezone` is an IANA name; null or "inherit" means inherit. `timezone.py` validates it with Python `zoneinfo` and requires the native OS database. On a guarded launch, the launcher copies the environment, overrides `TZ` when configured, removes an inherited alternate `TZDIR` for that explicit override, and uses `execve` with the original argument vector. The parent environment is not mutated. `set-timezone` shares the journaled config-update mechanism with `set-country`. Invalid configuration, including a malformed timezone, denies checkpoints. The timezone does not change the allowed country.

A new environment cannot retrofit an existing Desktop singleton or its already-running workers. The setting therefore requires a complete app restart, not just another window or a user-service restart. Application-specific timezone rendering must be checked in the real app. No global timezone or clock changes are implemented.

References: [Python zoneinfo](https://docs.python.org/3/library/zoneinfo.html) and [GNU libc TZ environment handling](https://sourceware.org/glibc/manual/latest/html_node/TZ-Variable.html).

## Desktop semantics

An entry is a candidate when its nonlocalized Name contains the word Claude or Exec contains Claude. These matches are only hints. A candidate whose command contains a web URL or a browser app flag, or whose executable has no Claude-named file or directory below the home directory, is reported as unprotected and never registered for termination, because its executable is shared with unrelated applications. Entries are deduplicated by FreeDesktop ID in XDG precedence order. Hidden entries mask lower-priority entries. User entries retain their exact path, including nested paths. Standard autostart directories use the same conservative discovery. Multiple supported candidates are all planned; one unsafe Claude entry fails the plan rather than silently reporting complete protection. Doctor repeats discovery and reports supported entries, actions and autostart entries that are not guarded.

The manifest records the stable launcher path of each guarded command, such as `~/.local/bin/claude`, next to its validated executable. When a launcher later resolves to a different native file in the same Claude-named directory, owned by the user or root and not group- or world-writable, the Desktop launcher and the monitor accept it as an update. The monitor keeps earlier versions registered for processes that are still running. Any other target blocks the launch and is reported by doctor. Older manifests are migrated by reading the launch paths from their recorded desktop edits.

The installed bootstrap pins the home, data, config and state directories chosen at installation. Hooks, launchers and the monitor therefore read the same policy regardless of the caller's XDG variables, which are still passed unchanged to Claude.

A denied `Verdict` is marked transient when nothing contradicts the policy. Examples are timeouts, DNS or connection failures, HTTP 429 or 5xx responses, no public route, a kill switch blocking every family, or a route change that came with no mismatch. A runtime monitor check that fails transiently is repeated after 1 and then 3 seconds. After three failures in a row, or at once when no route exists, the monitor enters offline mode: running apps stay open and it keeps checking at the normal interval. Mismatches and any other failure still close Claude. An allowed verdict records the routing fingerprint it verified. On Linux, `ip monitor` wakes the monitor on route, rule and address events without moving the periodic schedule. When the routing fingerprint differs from every network that was verified, or tolerated while offline, the monitor sends SIGSTOP to Claude processes without a controlling terminal. It checks up to three times, allowing further attempts to start for about 10 seconds after the initial check; an in-flight check can extend the pause. It then sends SIGCONT, or closes Claude on a mismatch. A network that is unreachable during that pause is tolerated as offline. Terminal sessions are left running, because stopping a foreground job returns the terminal to the shell. Their hooks deny prompts and tools until verification passes. Termination resumes a paused process right after SIGTERM so the signal can be handled.

The monitor publishes readiness in `monitor-health.json` with its PID and process start time. Desktop launches require a ready monitor whose identity matches a live process, so a file left by a previous monitor cannot admit a launch. If the manifest cannot be read, the monitor keeps checking processes with the last valid manifest, reports itself degraded and recovers when the file changes.

The implementation adds a quoted guard prefix before the original Exec string, preserving the desktop environment's field-code expansion. It does not execute a shell to interpret Exec. Main and action Exec lines are tracked separately for reversal, even when text is identical. DBusActivatable is explicitly changed to false when present. MIME types, action names, icons, categories and desktop-file IDs are unchanged. MIME cache contents therefore need no installer rewrite. Desktop menus typically watch the applications directory; a logout/login may be necessary for stale launcher caches.

A native ELF main executable is required; every action must resolve to that same executable. A custom action targeting another program is rejected so its unrelated running instances cannot be selected for termination. Generic interpreters and wrappers are refused because their `/proc/exe` could identify unrelated applications. Installer output is a reviewable list of the candidates. This conservatism means some custom installations are unsupported; there is no unsafe fallback launcher claiming to replace an original. Filename-ID collisions within one directory tree are ambiguous under the specification and must be reconciled by the user.

On reinstall, a pristine generated system override is refreshed from the current system source. A customized user override can retain unrelated edits; simultaneous changes to both the package source and user override require reconciliation. The original package is never written. Source changes are reported by doctor. Deleting or moving the source entry without updating the installation is a conflict, not permission to launch an unknown executable.

## Ownership and transactions

`install --country CODE` persists the initial selection; an omitted option keeps the existing selection on reinstall. `set-country CODE` uses the installation lock, atomic write and backup journal to update only the country in config.json. Hooks and wrappers read config.json dynamically. The monitor reloads it each cycle, including timing values, so edits do not require service restart or hook replacement. Its generated unit does not pin interval/grace flags. Explicit flags on a manually started monitor override its timing for that invocation only. An invalid config yields a denied cycle with zero termination grace and a five-second recheck; the process continues to observe repairs.

Installation is serialized with an advisory lock. Every mutation is preceded by an atomically replaced, fsynced backup journal containing before/after bytes and original mode. Manifest records retain the initial before-image across repeat installations. The installer rolls back only if a path still contains the bytes it wrote. It does not overwrite concurrent user edits during rollback.

Uninstall plans all conflicts first. Hooks are identified through their guard executable/status message and removed only as exact owned groups. Changed commands, moved events, modified handlers or combined groups require reconciliation. Original settings bytes are restored only if the structurally cleaned settings equal the original data; otherwise current data is written without the owned groups. Shell blocks and desktop launch-line changes can be removed while preserving unrelated edits. Managed Python/service/wrapper file edits are conflicts.

A power loss/SIGKILL cannot execute an in-process rollback. Journals survive for manual recovery. Advisory locks cannot prevent a third-party editor from changing the same file between the last read and rename. Configuration files should not be edited during installation. Symlinked target files are rejected rather than replacing a link or guessing its owner's intent.

## Future portability

Only the Linux adapters are implemented. Platform abstraction points are route discovery, application registration, process inspection, service lifecycle and shell integration. macOS/Windows require their own reviewed implementations; the CLI rejects non-Linux systems.

## Stronger isolation: investigated, deferred

A robust opt-in design could create a dedicated network namespace and veth for the app, use a privileged helper to manage scoped nftables rules, and put the complete application tree under a dedicated service/cgroup. Egress would begin denied and require a short-lived authorization from checks made **inside that namespace**. Both IP families, DNS, proxy configuration, desktop D-Bus sockets, browser/file helpers, and inherited/existing sockets need explicit treatment. Revocation must precede established-connection allow rules or flush only the scoped connection state. A crashed verifier/helper must expire authorization automatically.

This requires root capabilities, namespace/cgroup lifecycle tests, a privilege-separated narrow helper protocol, cleanup of only uniquely owned resources, and a recovery command that removes only those resources. Desktop single-instance reactivation outside the namespace must be prevented, not just observed. Broad UID firewall rules would affect unrelated programs. Generic owner-based nftables snippets and an untested proxy would not meet these requirements. No network-isolation mode or firewall rules ship in version 1.
