# Installation, updates and recovery

Install Claude Desktop and Claude Code using Anthropic's supported installation instructions first. Their installer/package management is outside this project's scope. Run the guard installer as the owning desktop user. Never use sudo for this project.

From any directory, preview the latest release without cloning the repository:

```sh
curl -fsSL https://raw.githubusercontent.com/mhbahmani/Claude-Geo-Guard/master/install.sh | sh -s -- --dry-run
```

Omit `-s -- --dry-run` for interactive installation, or pass options after `sh -s --`. The bootstrap fetches source over HTTPS, runs the installer from a temporary directory, and removes the download on exit. When running from a checkout, the equivalent commands are:

```sh
./install.sh --dry-run
./install.sh --yes --country GB
```

Dry-run performs read-only environment, JSON, desktop and version checks. It does not write backups, install hooks, contact the provider, start services or launch Claude beyond querying CLI `--version`. Missing requirements or unsupported launch entries abort before mutations. By default all discovered Desktop installations/autostart entries are planned. `--skip-desktop` explicitly requests CLI/hooks-only integration.

The first installation defaults to GB if `--country` is omitted. Use an ISO alpha-2 code to select another country, such as `./install.sh --yes --country DE`. Reinstalling without this option preserves the saved country. After installation, `claude-geo-guard set-country FR` changes it atomically with a retained backup; the next launch, hook and monitor check use FR. No service restart is necessary. `status` displays `allowed_country`.

## macOS Desktop

Install from a logged-in graphical session with Claude.app in /Applications or ~/Applications. For a different location, use `./install.sh --desktop-app "/absolute/path/to/Claude.app"`. Open ~/Applications/Claude Guard.app for pre-launch checks and pin it to the Dock. Rerun installation after updating Claude. Preferences, wrappers and backups are under ~/Library/Application Support/claude-geo-guard. The monitor runs as a user LaunchAgent. Use `--skip-desktop` to install only the CLI and hooks.

## Linux prerequisites and paths

Use Ubuntu 22.04+ or Debian 12+ with curl, CA certificates and iproute2. Desktop monitoring requires a systemd user session. Install zenity (GNOME) or kdialog (KDE) for denial dialogs. See the [Linux reference](usage.md#requirements) for optional tools and supported launcher types.

Paths with normal XDG defaults:

| Content | Location |
|---|---|
| Private package/bootstrap | `~/.local/share/claude-geo-guard/lib/` |
| Guard and CLI wrappers | `~/.local/share/claude-geo-guard/bin/` |
| User-editable country, timezone and monitor timing | `~/.config/claude-geo-guard/config.json` |
| Internal ownership/restoration manifest | `~/.local/state/claude-geo-guard/manifest.json` |
| Timestamped journals | `~/.local/state/claude-geo-guard/backups/` |
| User hooks | `~/.claude/settings.json` |
| User desktop overrides | `~/.local/share/applications/` |
| User autostart overrides | `~/.config/autostart/` |
| Service | `~/.config/systemd/user/claude-geo-guard.service` |
| PATH blocks | `.bashrc`, `.profile`, Zsh `.zshrc` and `.zprofile` (respecting `ZDOTDIR`), plus existing `.bash_profile`/`.bash_login` |

The package is copied, not symlinked to the checkout. Wrappers use the installer Python interpreter by absolute path; install with a persistent system Python, not a disposable virtualenv. Python isolated mode ignores `PYTHONPATH`, and bytecode writes are disabled for installed entrypoints. Keep XDG paths consistent across the shell and graphical session. Desktop and hooks use absolute guard paths; they do not depend on the desktop's PATH finding the wrapper.

While Claude is closed, the Linux monitor sleeps between lightweight executable-path scans and skips network checks. A guarded launch starts the monitor if it is stopped. The monitor unit is enabled on `default.target` of the user manager. No root service or linger setting is installed. Sessions that have no user manager cannot use Desktop monitoring. You can run `claude-geo-guard monitor` for diagnostics, but a foreground monitor does not satisfy the guarded Desktop launcher's service-active requirement.

## Application timezone

Use `./install.sh --yes --country GB --timezone Europe/London` to configure country and timezone together. The timezone is optional and defaults to inheriting the launch environment. Reinstall without `--timezone` to preserve it, or pass `--timezone inherit` to clear it. Later, use `claude-geo-guard set-timezone ZONE` with an IANA name or `inherit`. The change is journaled and only updates config.json; hooks and shell settings are not rewritten.

Install the OS `tzdata` package if a selected timezone is unavailable. Fully quit and relaunch Claude Desktop and start new CLI sessions to apply the new environment. Existing processes, the operating system timezone and the systemd user manager are unaffected. Select the timezone explicitly; a country can span multiple timezones, so the guard does not guess one.

## Updating

Review the new source, run checks, then rerun installation. config.json is the only runtime preference source. If it is missing from an installed setup, restore it from backup or pass `--country` and `--timezone` (use `inherit` for the system timezone). Omitted timings default to a 15-second interval and a 5-second grace period. Other missing preferences also return to defaults; no settings are imported from the ownership manifest. The manifest preserves initial before-images and checks owned-file conflicts. System overrides are refreshed from their current package entries when still pristine. New executable paths are validated. Restart existing apps when updating so subprocesses and hooks use current code. Simultaneous package/user override edits require reconciliation; the installer will not guess.

## Normal rollback

An exception during installation stops the newly configured service, rolls back files whose content is still the installer's output, reloads the user manager and restores previous service activity where applicable. Backups remain. Error output gives any conflicts and the recovery journal. Empty directories are harmless and are not recursively deleted.

Uninstall similarly checks conflicts before service changes, removes owned groups from current settings, and keeps user edits. Pre-existing or edited config.json files are retained, including through repeat installs; an untouched installer-created config is removed. If a guard hook was edited, combined or moved, put its exact installed group back using the manifest as a reference, retaining your custom handler in a separate group, then retry. Never remove your existing custom handlers to make a test pass.

## Recovery after interrupted installation

SIGKILL, power loss or storage failure may prevent automatic rollback. Journals contain ordered records with absolute path, base64 `before`/`after` contents and original mode. They are sensitive backups, not executable scripts.

1. Close Claude using its normal UI. Stop the guard unit if it exists: `systemctl --user stop claude-geo-guard.service`.
2. Inspect the newest journal under the state backup directory. Work in reverse order. Restore an entry only if the current file still exactly matches its recorded `after` content; otherwise preserve it and reconcile manually.
3. Decode `before` into a temporary file and atomically replace the affected file with its recorded mode. A null before-image means that file was newly created; remove it only if its contents still match the after-image. For settings edited since installation, merge/remove exact guard groups instead of restoring the whole before-image.
4. Restore/remove the corresponding manifest only after its files are reconciled. Run `systemctl --user daemon-reload` and restore the previously intended enable/activity state.
5. Rerun `./install.sh --dry-run`, then install or uninstall as appropriate. Open a new shell and run doctor.

There is intentionally no blindly replaying recovery command that overwrites newer user changes. Retain the journal until recovery and manual launch tests are complete.
