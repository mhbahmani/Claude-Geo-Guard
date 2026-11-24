# Contributing

Use Python 3.10+ on Linux. Keep runtime dependencies limited to the standard library and documented host utilities. Do not run the installer against your actual home while developing.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
make check
make package
```

Tests isolate HOME/XDG directories and replace network/systemd utilities. Real native child processes in termination tests are explicitly created by the test. Never use process-name killing, a live provider request, or a real user's settings/service in ordinary tests. The live smoke command is opt-in.

Changes to installer ownership, rollback, hook schema, desktop Exec parsing or process identity require regression tests for existing custom configuration and failure paths. Keep exact event/version claims tied to current official documentation. Include manual GNOME/KDE results when claiming new launcher support. Do not describe process monitoring as strict network enforcement.

Before proposing a release, run tests, Ruff, ShellCheck, desktop validation and packaging checks, review SECURITY.md, and complete docs/desktop-testing.md on supported environments. Do not include actual user paths, settings, tokens, backups, addresses or invented maintainer identities in fixtures or reports.
