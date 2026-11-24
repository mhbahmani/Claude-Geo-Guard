# Security

This is a cooperating-user, best-effort guard. It is not kernel-level network enforcement. Read [the security model](../docs/security-model.md) before use. Version 0.1.x is the initial implementation; no unsupported platform or remote-session guarantee is made.

Report suspected vulnerabilities through the repository host's private vulnerability reporting feature when the published repository enables it. Until a private reporting route is available, avoid posting exploit details, credentials, settings or backup journals in public issues; ask for a private contact route using a minimal description. No maintainer identity or private contact address is assumed here.

Useful reports explain the affected revision and distribution, the violated documented guarantee, minimal reproduction with synthetic data, and whether existing user configuration is at risk. Expected limitations such as a user editing their own hooks, direct original executable launch, or traffic between monitoring polls are documented design boundaries. Accidental deletion of unrelated hooks, wrong-process termination, TLS verification bypass or falsely reported protection are security-relevant bugs.

No telemetry or analytics is collected. Backups can contain sensitive user configuration; retain and share them carefully.
