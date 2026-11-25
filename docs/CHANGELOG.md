# Changelog

## 0.7.3

- Cursor Agent can import the guard hook and submit prompts or use tools without a geo-check. Claude Code still checks its own hooks when launched from Cursor’s terminal, and Claude Desktop retains its launch and runtime checks.

## 0.7.2

- When a runtime check fails, the monitor requests the error window as it starts closing Claude Desktop. The window explains the failure while shutdown proceeds.
- On Linux, the monitor uses the graphical session's current display settings and sends a critical desktop notification. On macOS, the runtime alert requests focus and stays open until dismissed.

## 0.7.1

- On Linux, the installer marks guarded Desktop menu entries as user-managed so Claude Desktop preserves the pre-launch check when it refreshes its shortcuts. Rerun the installer to repair an affected entry.
- Desktop shows a progress window during verification. The CLI shows animated dots in the terminal and writes a single status line when stderr is redirected. If verification fails or times out, Claude stays closed.

## 0.7.0

- Guided setup asks whether to enable protection immediately, defaulting to yes on first install. Use `--disabled` for an unattended installation with protection off.
- Switch protection on or off with `claude-geo-guard enable` and `claude-geo-guard disable`. While disabled, the CLI and Desktop launchers show the enable command, hooks allow actions, and the monitor skips enforcement.
- The project is now licensed under Apache 2.0.

## 0.6.2

- macOS alerts explain why the guard closed Claude, show the available country and IP details, and suggest how to recover.
- On Macs without an IPv6 route, the guard continues checking IPv4 even when the route command reports the missing route with a successful exit status.
- The installer waits for macOS to finish unloading the monitor’s LaunchAgent before starting it again, fixing a race during reinstall.

## 0.6.1

The guard now waits up to 30 seconds for Claude Code to report its version. If the check times out, the error includes the command to run for diagnosis.

On Linux, the guard continues checking IPv4 when a VPN reports an unreachable IPv6 default and only local IPv6 routes are available.

## 0.6.0

Installation checks how a fresh interactive Zsh or Bash resolves `claude` and warns when a function, alias or different executable may bypass the guard. Guarded terminal launches report success or the reason for blocking on stderr.

CLI version errors identify the executable and distinguish an older Claude Code release from unrecognized version output.

## 0.5.0

On Linux, the idle monitor uses a lightweight executable scan and skips network checks while Claude Desktop is closed. Opening Claude through the guarded launcher starts a stopped monitor. If startup fails, Claude stays closed.

After a successful guarded launch, a popup shows the verified country and public IP. Claude opens immediately. Click OK to dismiss the popup, or let it close after five seconds. The existing `success_notifications` setting controls it.

## 0.4.0

Run `./install.sh` without flags for guided setup. Press Enter to accept the displayed default or keep your saved value on reinstall. The installer explains which destinations see your public IP and previews the changes before applying them. Use `--yes` for unattended installation.

When recovering a missing config, the monitor interval defaults to 15 seconds and the termination grace period to 5 seconds. On Linux, reinstalling reapplies protection if a desktop launcher exactly matches its saved original. Unrecognized edits are preserved and the installer reports the conflicting file.

## 0.3.0

Verify anonymous destination traces from claude.ai and api.anthropic.com. General egress verification remains enabled by default. Add --split-tunnel for routed VPNs and reject local hosts-file overrides for protected domains.

## 0.2.0

Adds a macOS Finder launcher and a user LaunchAgent for best-effort runtime checks. Installers select the latest GitHub release by default and master with `--dev`.

- Check country and public IP together through ifconfig.io/all.json; add optional exact allowed_ips matching and show returned IPs beside countries on success and failure.
- Add optional success notifications for guarded Desktop launches only; runtime recovery remains silent and never restarts Claude.

- Separate user-editable XDG config.json from internal installation state; reload country, timezone and monitor timing settings, preserve manual edits, and fail closed on invalid configuration.

- Show runtime closures in a nonblocking native error window; keep one open at a time, continue monitoring, and close the helper with the service.
- Record termination events and safe per-family provider error categories, curl exit codes and HTTP statuses in the journal.

- Refine Desktop denial dialogs with native typography, emphasized country facts, specific recovery guidance, a Close action and optional technical details. Keep unknown locations distinct from country mismatches.

- Show Desktop launch failures in a Zenity/KDialog error window, including allowed and detected countries; retain fail-closed behavior if a dialog is unavailable.

- Add optional `install --timezone ZONE` and `set-timezone ZONE` for new guarded Claude processes; `inherit` clears the override without changing the system timezone.

- Rename the project, command, package, user service and installation paths to `claude-geo-guard`.

- Add `install --country CODE` (default GB on first install) and `set-country CODE` for later changes.
- Share the saved country across launchers, hooks and monitor checks; retain it on reinstall.
- Report the selected country and deny invalid policies or changes during verification.

## 0.1.0 — 2026-11-14

- Initial Linux implementation targeting Ubuntu 22.04+ and Debian 12+.
- Independent IPv4/IPv6 country verification, fail-closed launchers and Code hooks.
- Metadata-preserving desktop overrides and pidfd-based runtime process monitoring.
- User-local journaled installation, repeat installation and conflict-aware removal.
- Headless unit/integration coverage, CI, packaging and manual desktop acceptance procedure.
- Best-effort enforcement only; kernel network isolation and real GNOME/KDE/Claude acceptance remain outside automated validation.
