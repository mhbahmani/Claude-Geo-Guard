# Validation record

Latest local automated validation on 2026-11-24:

- Host: Ubuntu 24.04.5 LTS; Python 3.12.3.
- `make check`: **303 tests passed in 71.96 seconds**; Ruff 0.17.5 lint/format checks, ShellCheck 0.11.0 and Python compilation passed.
- `make package`: wheel and source distribution built; `twine check` passed for both.
- Runtime modules also passed a Python 3.10 grammar parse. This is not a substitute for running the CI interpreter matrix.
- Earlier read-only inspection (2026-11-14) of installed `claude-desktop` package 5.11492.5 found a native executable and three Exec paths (main, New Chat, New Code). A temporary transformed copy passed `desktop-file-validate`, and reversal exactly reproduced the source. The validator emitted only the vendor's multiple-main-category hint. No vendor files, real user hooks, shell files or services were modified.

## What the automated results establish

The tests cover timezone validation, environment inheritance and isolation, native-process daylight-saving behavior, timezone changes and reinstall preservation, consistent renamed commands/packages/services, default/custom country installation, country changes without restarting the monitor, retained selections on reinstall, invalid or changing policy rejection, mocked family-specific network policy and errors, route changes, proxy refusal, HTTP status and country validation, conservative desktop discovery/overrides/actions, settings merging and preservation, conflicts, atomic write failure, rollback, repeated installation/uninstallation, CLI argument/exit handling, and user-manager failure behavior.

End-to-end tests execute the actual shell installer and installed wrappers inside temporary HOME/XDG directories with fake provider, route, CLI, notification and systemd commands. They launch real native test processes, verify wrapper signal behavior, and run the actual monitor to demonstrate that a denied check terminates the selected test application while leaving an unrelated process alive. Separate pidfd tests cover graceful/forced termination and PID reuse defenses.

Desktop denial tests also exercise the installed wrapper with a fake Zenity helper and verify country details, continued denial, GNOME/KDE helper selection, text escaping, dismissal, missing/broken display fallback, and terminal-only hooks/CLI behavior. Tests clear inherited display variables and never open real windows. Actual window rendering still requires the manual GNOME/KDE check.

The revised dialog adds tests for cause-specific recovery, unknown-country labeling, optional details, native close responses, and backslash/markup injection. Seven message variants were also parsed by the installed GLib/Pango implementation, confirming valid markup and exact visible text preservation. The user confirmed the earlier error window appeared successfully; the revised layout, theme/scaling behavior and accessibility still require visual acceptance in a desktop session.

Runtime closure coverage verifies that a simulated curl timeout stops only the protected native test process, invokes the installed fake dialog helper, and emits classified verification/termination journal messages. Unit tests verify that an open window does not block subsequent checks, duplicate windows are suppressed, later incidents can show a fresh window, UI failure does not stop the monitor, and shutdown terminates even an unresponsive dialog helper. Two runtime message variants also passed native GLib/Pango parsing. Provider-error tests ensure unchecked raw stderr, response bodies, command arguments and embedded address strings do not leak into error reasons. Validated returned IPs are now deliberately included in verdicts and UI messages. Real user-service window display remains a manual check.

Config tests cover direct edits, strict validation, retained preferences across reinstall/uninstall, missing configuration, rollback, concurrent planning edits, and live monitor timing reload with denial on malformed saves. IP tests cover exact dual/single-stack matching, canonical IPv6, invalid addresses and JSON, country/IP consistency, policy changes during verification, and partial results when one family fails. Success confirmation tests cover enabled/disabled Desktop launches and verify the monitor never emits success notifications. Installed-wrapper integration verifies both a successful confirmation and a denied dialog with IP/country text. Updated runtime and launch IP message variants passed native GLib/Pango parsing.

The ifconfig.io documentation and a read-only request confirmed its all.json endpoint supplies country_code and ip together. Normal automated tests remain fully mocked and independent of live provider connectivity.

## Not verified locally

- Real GNOME/KDE search, favorites, actions, MIME/URL associations, autostart and D-Bus behavior.
- Real Claude execution of all three hooks, failure/timeout blocking, Desktop's bundled Code version, and timezone rendering in real Claude components.
- Real systemd user-manager enable/restart/logout lifecycle; lifecycle commands were mocked in automated integration tests.
- Hosted GitHub Actions, Ubuntu 22.04/Debian 12 containers, and other Python interpreters. Workflows are provided for these runs.
- Live ifconfig.io connectivity or actual UK/non-UK VPN transitions. Normal tests intentionally use no live provider.
- Remote/SSH/cloud sessions, nonnative launchers, or strict network enforcement.

Before a public production-support claim, run the CI matrix and [manual acceptance checklist](desktop-testing.md) on the intended supported desktops. Kernel network isolation remains explicitly deferred. This record describes performed checks, not certification or a guarantee against bypasses.
