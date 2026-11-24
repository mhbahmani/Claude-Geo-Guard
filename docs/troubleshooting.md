# Troubleshooting

Start with `claude-geo-guard doctor`, `type -a claude`, and `systemctl --user status claude-geo-guard.service`. Doctor distinguishes installed configuration, the live country check, and behavior that cannot be verified headlessly. Its zero exit status means its checks passed, not that manual desktop acceptance is complete.

| Symptom | Action |
|---|---|
| Desktop does not open and no error window appears | Reinstall the updated guard with `./install.sh --yes`. Install `zenity` (GNOME) or `kdialog` (KDE) and launch through Applications in a graphical session. `doctor` reports the installed dialog helper; this does not prove a window can be displayed. If the helper fails, stderr and an attempted critical notification remain available. |
| Country matches but IP is denied | Check `allowed_ips` in config.json. Every usable IPv4/IPv6 family must have its returned address listed. `[]` disables only IP matching. |
| Success message missing | Check `success_notifications`, install `libnotify-bin`, and review Do Not Disturb settings. Success confirmations are notifications; denials remain windows. |
| Window says allowed GB, detected PL | The guard is working: the provider reports Poland for that address family. For a denial test this is expected. To allow a launch, the connection must verify the configured country. Dismissing the window does not override the policy. |
| Timezone change has no visible effect | Fully quit Desktop and relaunch through Applications, or start a new CLI session. A new window may reuse the old process. Components that ignore TZ are not controlled by this setting. |
| Timezone rejected | Use a case-sensitive IANA name such as Europe/London and ensure the OS tzdata package is installed. `set-timezone inherit` clears the override. |
| Wrong allowed country | Run `claude-geo-guard set-country CODE` with an ISO alpha-2 code, then inspect `status`. Use GB, not UK. |
| Guard configuration invalid or unreadable | Fix `~/.config/claude-geo-guard/config.json` using all four documented keys and valid JSON. `doctor` reports the problem. Do not edit or delete the ownership manifest. If the config is missing, restore it or reinstall with `--country` and `--timezone`. Omitted interval and grace default to 15 and 5 seconds respectively. |
| Verification says proxy ambiguity | Inspect HTTP(S)/ALL proxy variables in the shell **and** user-manager environment. Use a normally routed VPN if appropriate. Do not assume an app's internal proxy is checked. |
| One family differs from the configured country | Fix the VPN's dual-stack routing. The guard intentionally denies mixed egress. |
| Timeout, DNS, TLS, provider outage | Repair connectivity/CA trust/provider availability. No fail-open switch exists. Never disable certificate verification. |
| Claude closes while working | Read `journalctl --user -u claude-geo-guard.service -n 100 --no-pager -o short-iso`. The runtime error window also links to this command in Details. A timeout/DNS/TLS/HTTP failure triggers closure even if earlier checks were allowed. |
| Runtime window missing, but launch window works | Check the journal for `runtime_dialog_unavailable` or `runtime_dialog_failed`. The systemd user manager must have the graphical session environment. From a terminal inside that session, run `systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XAUTHORITY XDG_CURRENT_DESKTOP`, then `systemctl --user restart claude-geo-guard.service`. Undefined variables may be skipped with warnings. Install Zenity or KDialog if absent. |
| Old log says only CalledProcessError | That older version discarded the curl exit code. The exact historical cause cannot be recovered. Reinstall the updated guard for classified future failures; no raw provider stderr is logged. |
| Monitor inactive | Inspect `journalctl --user -u claude-geo-guard.service`; repair errors, then `systemctl --user restart claude-geo-guard.service`. |
| User manager unavailable | Install within a normal logged-in supported desktop session; do not substitute a root service. For explicit CLI-only use, select `--skip-desktop`. |
| Applications uses an old launcher | Log out/in to refresh caches; inspect favorites for hardcoded executable commands rather than desktop-file IDs. Perform both GNOME/KDE checklist activation tests. |
| D-Bus or hotkey launch bypasses guard | Those independent paths are outside user Exec overrides. Disable that path or use the standard Applications entry. No strict interception claim applies. |
| CLI resolves original binary first | Open a new shell, inspect `type -a claude`, fix aliases/functions or later PATH resets, and clear shell command hashing. |
| Original CLI missing after update | Fix PATH or reinstall with `--claude-binary /new/path`; explicit paths are not automatically guessed elsewhere. |
| Unsupported Claude version | Upgrade to a stable compatible version. This installer refuses a claimed fail-closed hook setup on old or unrecognized versions. |
| Hooks show installed but never run | Restart the session; check `/hooks`, managed/project settings and disabled hooks. Verify the Desktop bundled engine independently. |
| Hook errors allow an action | Stop relying on that engine until upgraded and manual failure tests pass. Version gating of the standalone CLI does not prove Desktop's runtime behavior. |
| JSON invalid or symlink target refused | Preserve and repair the original file yourself, or resolve intentional symlink ownership. The installer never resets settings. |
| Script/Flatpak/Snap launcher refused | Use the official native package entry. Shared interpreter paths cannot identify only Claude processes safely. |
| Package source and override both changed | Compare current files with journal/manifest, preserve user metadata, reconcile the source Exec update, then rerun installation. |
| Uninstall reports edited hook or file | Review the exact conflict, move custom handlers into separate groups, restore only the owned portion, retry. Do not restore a whole old settings backup. |
| SSH/cloud session behaves differently | Hooks execute on the session host. Remote environments are not configured or verified by this installer. |

The monitor journal reports reason categories without observed public addresses. CLI check output and desktop messages now include the returned public IP, and configured allowed IPs are stored in your preferences. If sharing a diagnostic report, inspect it first: paths, application locations, desktop environment and version details are included. Backup journals contain full historical settings and must not be posted publicly.

Provider failure categories use [curl’s documented error codes](https://curl.se/libcurl/c/libcurl-errors.html): exit 6 is DNS lookup failure, 7 is connection failure, 28 is timeout, 35 is TLS handshake failure, and 60 is certificate validation failure. Exit 22 indicates an HTTP error; the recorded HTTP status can distinguish rate limiting (429) from server errors. Do not assume a timeout means the country changed.
