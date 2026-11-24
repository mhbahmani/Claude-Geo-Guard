# Security model

This tool assists a cooperating user in avoiding Claude usage when public egress cannot be verified as the configured country (GB by default). It is not an adversarial sandbox, network firewall, compliance attestation, or physical-location proof.

## Provided at implemented checkpoints

- Only successful HTTP 200 responses containing a normalized ISO 3166-1 alpha-2 code matching the configured country allow a checkpoint. Every usable family must verify. HTTP/DNS/TLS errors, timeouts, invalid codes, missing dependencies, detected route changes, no usable routes and proxy ambiguity deny.
- Curl validates certificates through its normal trust store; no TLS bypass flag is used. Redirects are not followed. `-q` suppresses user curl config. The provider learns the request's public egress IP as with any HTTPS request; this project does not request or persist it.
- Wrapper allow uses `exec` with preserved argv; denial does not execute the target. Original binaries are not overwritten.
- Supported Code hooks return exit 2 for denial. `onFailure: block` asks compatible Code engines to block spawn failures, timeouts and invalid output as well. Standalone Code versions below 2.1.295 are rejected. Desktop's engine and actual failure semantics need manual testing.
- The monitor uses same-UID, executable and ancestry evidence, not a process name match. pidfds and start-time revalidation protect against stale PID reuse during signaling. SIGTERM is followed by SIGKILL after the configured grace.
- Existing unrelated configuration is retained; conflicts are explicit. Backups are mode 0600 and can contain sensitive settings. Protect them as carefully as the original settings.

## Not provided

- **Per-request network enforcement.** Polls default to a 15-second wait between cycles, plus verification time, followed by up to 5 seconds of graceful termination. Requests may be made during all those windows. No guaranteed upper response bound applies when the OS/service is suspended, overloaded or unhealthy.
- **Global interception.** Direct binaries, desktop hotkeys, custom pinned commands, D-Bus callers, background activators, remote clients and session restore can bypass Exec overrides. The monitor may detect matching native Desktop processes afterward, but CLI runtime protection relies on hooks.
- **All descendants in every circumstance.** Observed ancestry is tracked while the monitor lives. A process can fork and detach before observation; a restart loses previously remembered ancestry. A new subprocess can appear during termination. Another supervisor may restart an app; this project does not restart it but cannot prevent that supervisor from doing so.
- **Exact routing equivalence.** Public representative destinations determine route availability. Policy routing, split tunnels, destination-specific VPNs, network namespaces, application-specific proxies, connection migration and provider-specific routes can differ from actual Claude traffic. Change detection can miss changes that happen and revert between snapshots.
- **Anti-tampering.** The user can change the allowed country with `set-country`, remove or edit hooks, change PATH, change trust roots/provider resolution, replace binaries, disable the service or modify the code. A privileged attacker can do more. Exact path identity assumes those executables remain trustworthy.
- **Location truth.** With the default GB policy, a UK VPN can allow a physically non-UK user. Provider inaccuracies or outages can deny a real UK user. There is no secondary provider fallback or cached allow.
- **Remote laptop-location checks.** SSH/cloud sessions run checks where their hooks execute. These are not automatically installed remotely and would evaluate remote egress if installed.

## Application timezone

The optional timezone only sets `TZ` for new guarded Claude processes. It is a launch preference, not a location check or timezone-enforcement boundary. It does not change physical location, the provider result, the system clock/timezone, remote sessions or existing processes. The monitor does not alter application timezones. It does validate the complete config, so invalid timezone syntax causes denial just like other invalid settings.

## Privacy and load

No analytics or telemetry. Check output and desktop UI include returned country codes and public IP addresses. Explicit allowed_ips are stored in config and backups; automatically observed IPs are not written to a history file. Desktop notification history and launch stderr may retain displayed IPs according to the desktop environment. Monitor journal messages contain verdicts, country codes, reason categories, process counts and termination events. Provider failure reasons include the address family, curl exit code and validated HTTP status when available. Runtime dialogs execute in a separate worker and never suspend monitoring; only one can be open at a time, with further events recorded in the journal. Curl's raw failure output and route snapshots are not logged. Each active monitor cycle makes one request per usable family; each hook/launch makes its own live requests. Heavy tool use can hit provider rate limits, which deny checks and may close Desktop. There is deliberately no positive-result cache extending the acceptance window.

## Host trust and recovery

Use the official Claude distribution, a trusted Python/curl/ip installation, trusted DNS/CA configuration and a protected user account. Do not edit configuration concurrently with installation. A normal installation failure rolls back its changes, conditional on still owning the written content. A killed installer needs journal-guided recovery; see installation.md. Never restore an entire historical settings file over a newer edited one.

Kernel-enforced app isolation would need a separately privileged, tested network-namespace/cgroup design with default deny and expiring authorization. It is deferred; this repository intentionally contains no firewall rules.

## User-editable configuration

Preferences live in XDG config.json, separate from installer state and backups. Country, timezone, interval and grace are required; allowed_ips and success_notifications are optional. All settings are validated at checkpoints. Missing installed config, malformed JSON, duplicates, unknown keys, nonfinite or out-of-range timing and invalid country/timezone values deny access. Editing the country can deliberately change the policy; this remains a cooperative user guard. Partial editor saves can trigger a closure; prefer atomic saves. Installation/CLI writes are atomic and journaled, and uninstall preserves pre-existing or edited preferences. The manifest is never a fallback source for runtime preferences.

Country and IP are taken from a single `https://ifconfig.io/all.json` response per family. Only the country_code and ip fields are used; forwarded/header fields are ignored. IP matching is optional and exact, and cannot override country restrictions. Every usable family must satisfy the enabled checks. Unknown or invalid IP data denies access; partial valid results may be displayed on denial, but do not authorize access. A policy change during a check denies the result. This still does not bind application traffic to the tested route or prevent traffic before the monitor reacts.
