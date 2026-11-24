# Changelog

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
