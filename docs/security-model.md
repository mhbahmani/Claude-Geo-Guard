# Security model

This tool assists a cooperating user in avoiding Claude usage when public egress cannot be verified as the configured country (GB by default). It is not an adversarial sandbox, network firewall, compliance attestation, or physical-location proof.

## Launch and hook checkpoints

- Launch checks require successful HTTP 200 responses with the configured country and allowed IP, for every usable address family. At least one Claude endpoint must verify each family; other required providers may fail transiently without denying that check. Any observed mismatch or hard failure still denies. Hooks also allow an entirely unverified transient result, as described below.
- Curl validates certificates through its normal trust store; no TLS bypass flag is used. Redirects are not followed. `-q` suppresses user curl config. The provider learns the request's public egress IP as with any HTTPS request; the guard reads it for policy evaluation.
- Wrapper allow uses `exec` with preserved argv; denial does not execute the target. Original binaries are not overwritten.
- Code hooks block a prompt with exit 2 and a tool call by ending the turn (`continue: false`), so the reason is shown to the user and never returned to the model. They reuse the shared verdict described in [usage](usage.md#cli-and-hooks): a monitor allow can be reused for one interval plus a 20-second check allowance; hook-only checks cache for 15 seconds. Policy and `/etc/hosts` freshness are checked at use, and an observed routing change invalidates the monitor cache. A hook can race detection of a routing change. `onFailure: block` asks compatible Code engines to block spawn failures, timeouts and invalid output as well. Standalone Code versions below 2.1.295 are rejected. Desktop's engine and actual failure semantics need manual testing.
- The monitor uses same-UID, executable and ancestry evidence, not a process name match. pidfds and start-time revalidation protect against stale PID reuse during signaling. On Linux, guarded Desktop launches run in a dedicated systemd user scope; its cgroup membership survives reparenting and new sessions. On macOS, SIGTERM is followed by SIGKILL after the configured grace.
- Existing unrelated configuration is retained; conflicts are explicit. Backups are mode 0600 and can contain sensitive settings. Protect them as carefully as the original settings.

## Runtime availability policy

Running applications stay open when verification fails only because of transient connectivity or provider availability. After three failed checks (or immediately with no usable route), the monitor reports offline mode and continues checking. A country or IP mismatch and other hard failures still stop Claude's traffic. A failed probe does not prove that Claude cannot send traffic: while offline mode is active, running applications may communicate over an unverified connection. Hooks follow the same rule and allow prompts and tools in that state; new launches remain blocked until verification succeeds. When one Claude endpoint verifies the allowed country and another only times out, the check passes. With a split tunnel, the unverified endpoint could take a different route; it is checked again at the next interval.

On Linux, a hard failure pauses Claude rather than closing it. Processes that can reach the network are stopped with SIGSTOP: the Chromium network service, the embedded Claude Code, tool and helper processes. The main, renderer and GPU processes keep drawing the window. Chromium renderers cannot open sockets themselves. The main process can, so while paused the monitor checks the window processes' sockets every two seconds and stops them too if one holds a non-local connection. Only a passing check resumes Claude. After the pause limit, the scope is frozen as a whole, killed with `cgroup.kill`, and its emptiness is confirmed. No grace period lets Claude send while it is closing.

Detected route changes pause the same processes while the monitor checks the new network. They resume after successful verification or a transient failure.

The enforcement point is the moment processes are stopped. Data already handed to the kernel, and requests already sent, cannot be recalled, and Anthropic's servers may keep processing them. A window process that connects while paused can send for up to two seconds before it is stopped. This is process control, not a network firewall.

## Not provided

- **Per-request network enforcement.** Polls default to a 15-second wait between cycles, plus verification time; on Linux, routing events start a check immediately. Requests may be made until processes are paused, and on macOS during up to 5 seconds of graceful termination. No guaranteed upper response bound applies when the OS/service is suspended, overloaded or unhealthy.
- **Global interception.** Direct binaries, desktop hotkeys, custom pinned commands, D-Bus callers, background activators, remote clients and session restore can bypass Exec overrides. The monitor may detect matching native Desktop processes afterward, but CLI runtime protection relies on hooks.
- **All descendants in every circumstance.** A Linux Desktop started through the guard is covered by its scope, including detached helpers. Without a scope (Desktop started directly or before this update, and on macOS), only observed ancestry is tracked; a process can fork and detach before observation, and doctor reports such sessions. Services Claude starts through D-Bus or systemd activation run in other units and are not covered. Terminal Claude Code sessions rely on hooks. Another supervisor may restart an app; this project does not restart it but cannot prevent that supervisor from doing so.
- **Exact routing equivalence.** Public representative destinations determine route availability. Policy routing, split tunnels, destination-specific VPNs, network namespaces, application-specific proxies, connection migration and provider-specific routes can differ from actual Claude traffic. Change detection can miss changes that happen and revert between snapshots.
- **Anti-tampering.** The user can change the allowed country with `set-country`, remove or edit hooks, change PATH, change trust roots/provider resolution, replace binaries, disable the service or modify the code. A privileged attacker can do more. Exact path identity assumes those executables remain trustworthy.
- **Location truth.** With the default GB policy, a UK VPN can allow a physically non-UK user. Provider inaccuracies or outages can deny a real UK user. There is no secondary provider fallback or cached allow.
- **Remote laptop-location checks.** SSH/cloud sessions run checks where their hooks execute. These are not automatically installed remotely and would evaluate remote egress if installed.

## Application timezone

The optional timezone only sets `TZ` for new guarded Claude processes. It is a launch preference, not a location check or timezone-enforcement boundary. It does not change physical location, the provider result, the system clock/timezone, remote sessions or existing processes. The monitor does not alter application timezones. It does validate the complete config, so invalid timezone syntax causes denial just like other invalid settings.

## Privacy and load

No analytics or telemetry. Check output and desktop UI include returned country codes and public IP addresses. Explicit allowed_ips are stored in config and backups; automatically observed IPs are not written to a history file. Desktop notification history and launch stderr may retain displayed IPs according to the desktop environment. Monitor journal messages contain verdicts, country codes, reason categories, process counts and termination events. Provider failure reasons include the address family, curl exit code and validated HTTP status when available. Runtime dialogs execute in a separate worker and never suspend monitoring; only one can be open at a time, with further events recorded in the journal. Curl's raw failure output and route snapshots are not logged. Each active monitor cycle makes one request per configured destination per usable family; each hook/launch makes its own live requests. Heavy tool use can hit provider rate limits, which deny launch and hook checks and are tolerated by the runtime monitor as transient failures. There is deliberately no positive-result cache extending the acceptance window.

## Host trust and recovery

Use the official Claude distribution, a trusted Python/curl/ip installation, trusted DNS/CA configuration and a protected user account. Do not edit configuration concurrently with installation. A normal installation failure rolls back its changes, conditional on still owning the written content. A killed installer needs journal-guided recovery; see installation.md. Never restore an entire historical settings file over a newer edited one.

Kernel-enforced app isolation would need a separately privileged, tested network-namespace/cgroup design with default deny and expiring authorization. It is deferred; this repository intentionally contains no firewall rules.

## User-editable configuration

Preferences live in XDG config.json, separate from installer state and backups. Country, timezone, interval and grace are required; allowed_ips and success_notifications are optional. All settings are validated at checkpoints. Missing installed config, malformed JSON, duplicates, unknown keys, nonfinite or out-of-range timing and invalid country/timezone values deny access. Editing the country can deliberately change the policy; this remains a cooperative user guard. Partial editor saves can trigger a closure; prefer atomic saves. Installation/CLI writes are atomic and journaled, and uninstall preserves pre-existing or edited preferences. The manifest is never a fallback source for runtime preferences.

Every required destination and usable address family must satisfy both country and optional IP policy. A policy change during verification denies the check. See [network checks](network-checks.md) for trace fields, probe privacy and DNS limits.
