# Ubuntu CLI and Desktop guard reliability

## Handoff

Review performed on Ubuntu 24.04.5 LTS at commit `323ebed`. At that revision, `make check` passed with 368 tests passed and 3 skipped. The installed modules matched the checkout, the user service was active and enabled, both installed desktop overrides matched the manifest, and the three CLI hooks were registered. A live split-tunnel check passed. Registration alone leaves real Claude hook execution unverified. The subsequent commit `81f257f` changed installer download progress and its tests. The test counts here belong to the reviewed revision.

Start with issues 1 and 3. They can silently apply the wrong policy or terminate unrelated work. Use the executable identification rules from issue 3 when implementing update handling in issue 2. The remaining changes and acceptance criteria below are proposals.

### 1. Installed hooks can silently use the wrong policy

**Evidence.** In a temporary installation configured for Germany and one allowed IP, the installed hook returned exit 2 for a simulated GB connection. With only the XDG data, config, and state roots changed, the same hook returned 0. Runtime path discovery made the installation look absent and selected the GB defaults with an empty IP allowlist.

**Fix.** Record absolute policy and state paths in the installed bootstrap. Thread those paths through every installed entry point, including both configuration reads in the checker. Keep defaults available during first-time setup. When installed configuration is missing or unreadable, deny and report the expected path. Keep the caller’s XDG environment intact for the application being launched.

**Acceptance.** Install under custom XDG roots, invoke the actual installed hook from a second environment, and assert that the original country and IP restrictions still apply. Repeat with a missing config and verify denial. Confirm that an atomic policy edit takes effect at the next check.

[Path discovery](../../src/claude_geo_guard/paths.py) · [Configuration loading](../../src/claude_geo_guard/config.py) · [Checker](../../src/claude_geo_guard/checker.py) · [Installed bootstrap](../../src/claude_geo_guard/installer.py)

### 2. Executable updates break the URL handler and process recognition

**Evidence.** The installed Claude Code URL handler used the stable path `~/.local/bin/claude`, while the manifest stored its resolved target ending in `versions/2.1.319`. In an isolated install, switching an equivalent symlink to a new version left the new target outside the monitor’s registered set. The desktop launch check rejects that target, but `doctor` reported healthy. The ordinary shell wrapper follows a separate launch path.

**Fix.** Store the stable launcher path, installation identity, and last validated executable. Share resolution and validation between the launcher, monitor, and diagnostics. Revalidate a replacement only when its identity changes. Accept recognized updates within the validated installation. Flag an unfamiliar target for repair. Preserve PID and start-time tracking for old processes that remain alive during the update. Run any CLI version subprocess before the final egress check.

**Acceptance.** Keep version A running, replace the stable symlink with version B, and exercise both the menu URL handler and shell wrapper. Verify coverage for A and B, rejection of an unrelated replacement, and an actionable diagnostic for any unresolved change. Include migration from an existing manifest.

[Installer registration](../../src/claude_geo_guard/installer.py) · [Desktop launcher](../../src/claude_geo_guard/desktop_launcher.py) · [Linux process matching](../../src/claude_geo_guard/platforms/linux/processes.py)

### 3. A shortcut can authorize termination of an unrelated application

**Evidence.** Discovery matches “Claude” in a desktop entry’s name or command, including a URL argument. A simulated browser shortcut to claude.ai registered its shared native executable. On denial, the monitor selects every same-user process running that executable and its observed descendants. The live manifest contained only Claude executables. The isolated test proved registration without terminating a browser.

**Fix.** Treat name and URL matches as discovery hints. Require positive evidence of a dedicated Claude installation before adding a termination target, for example a supported Desktop package and its owned executable, or a validated native CLI installation. Report ambiguous shortcuts. Revalidate legacy manifest records on upgrade before the new monitor uses them. Do package validation at installation or executable change and retain pidfd signaling on the hot path.

**Acceptance.** Install fixtures for native Desktop, native Code, and a Claude web shortcut pointing to a shared executable. Simulate denial against disposable processes. The dedicated targets should stop and an independent instance of the shared executable should survive. Repeat from a legacy manifest containing the erroneous registration.

[Desktop discovery and interception](../../src/claude_geo_guard/platforms/linux/desktop_entries.py) · [Process selection and signaling](../../src/claude_geo_guard/platforms/linux/processes.py)

### 4. A guarded CLI launch can start a session without its hooks

**Evidence.** The wrapper checks egress and CLI version, then forwards the caller’s arguments and environment. It does not verify the selected hook configuration. Claude Code documents startup modes that skip user hooks. In a CLI-only installation, a successful launch can therefore be the final checkpoint. This finding comes from the launch code and documented CLI behavior.

**Fix.** Before the network probe, validate the three guard-owned registrations in the settings the invocation will load. Handle bare mode, safe mode, the simple-mode environment setting, and alternate settings roots or sources. If the wrapper cannot establish hook coverage for a supported invocation, explain what prevents it. Parse options with their argument boundaries so ordinary prompt text passes through. Read settings once per launch and preserve unrelated hooks.

**Acceptance.** Cover missing and edited hooks, incompatible flags and environment, ordinary scripted prompts, and literal flag text in prompt arguments. In a disposable real Claude session, verify prompt submission, prompt expansion, and tool use are actually blocked on denial.

[CLI wrapper](../../src/claude_geo_guard/cli_launcher.py) · [Owned hooks](../../src/claude_geo_guard/settings_manager.py) · [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)

### 5. A malformed manifest can leave running applications unmonitored

**Evidence.** An installed monitor exited with code 2 when its manifest contained invalid JSON. It reads the manifest outside its configuration recovery block on every cycle. The generated user unit allows three starts per minute with a five-second restart delay. A persistent error can exhaust retries, and repairing the manifest does not itself restart the unit.

**Fix.** Retain the last validated executable list and tracked process identities in memory. On a failed reload, continue checking known processes against the current policy, mark health degraded, and retry the file with bounded backoff. On cold start without valid state, keep guarded Desktop launches blocked while recovery runs. Require initialized monitor readiness for launch and expose subsequent degradation to the launcher and doctor. Cache the parsed manifest across unchanged cycles and log health transitions once.

**Acceptance.** Test a malformed or missing manifest during an active session and at cold start. Restore it atomically and verify recovery without operator intervention. Check that stale readiness from a previous monitor cannot admit a new launch and that known running processes still receive policy checks during a bad reload.

[Monitor loop](../../src/claude_geo_guard/monitor.py) · [User service](../../src/claude_geo_guard/platforms/linux/systemd.py) · [systemd service readiness](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)

### 6. Doctor misses new unguarded desktop launch paths

**Evidence.** After an isolated installation, adding a second Claude desktop entry put it in the discovery output. Doctor still returned healthy with no issues because validation iterated over the old manifest. A package update can introduce the same gap with a new launcher or action.

**Fix.** Compare effective desktop entries, actions, and autostart commands with managed coverage. Include changed executable targets from issue 2. Distinguish positively identified Claude applications from ambiguous web shortcuts using issue 3. Report the affected entry and a repair action. Reuse conflict-aware install logic to preserve user customizations. Run full desktop discovery during diagnosis or repair, outside the prompt and polling paths.

**Acceptance.** Add a new entry, an action on an existing override, and an autostart entry after install. Each unguarded native Claude command should produce a diagnostic. Verify correct handling of XDG precedence, hidden entries, and user-edited overrides.

[Diagnostics](../../src/claude_geo_guard/diagnostics.py) · [Desktop entry discovery](../../src/claude_geo_guard/platforms/linux/desktop_entries.py)

### 7. One transient provider timeout can close a running session

**Evidence.** The available seven-day user journal contained 41 runtime checks and one termination sequence following an IPv4 timeout from the API trace endpoint. This is an availability cost of the current fail-closed policy. A timeout alone supplies no evidence that egress is safe.

**Fix.** Classify verification failures. Deny confirmed policy mismatches, invalid configuration, and ambiguous proxies immediately. For a transient transport failure, consider one retry of the failed destination and family within the existing verification deadline. Use a monotonic deadline, leave enough time for the retry, and revalidate routes and policy before accepting. Keep launches and hook actions blocked while pending. For an open application, deny at the deadline and apply the configured termination grace. Do not reuse an earlier successful cycle to authorize traffic.

**Acceptance.** Cover timeout followed by success, persistent timeout, mismatch alongside a timeout, and a route or policy change during retry. Assert the overall deadline, immediate denial on known mismatch, one request per destination and family on the successful path, and no repeat requests to successful destinations. Measure denial-to-termination latency including grace. Polls and retries still permit traffic from an already-running process before detection.

[Provider checks](../../src/claude_geo_guard/checker.py) · [Runtime enforcement](../../src/claude_geo_guard/monitor.py) · [Security model](../security-model.md)

## Decision for long-running CLI sessions

CLI-only installations currently rely on launch and hook checkpoints. If the intended guarantee includes detecting route changes while a native CLI is idle or a tool is already running, implement runtime monitoring for CLI-only installs as a separate feature. Define the behavior when a user systemd manager is unavailable before changing CLI-only installation requirements.

One approach is to register the wrapper PID and start time before exec. The monitor should verify the resulting executable, handle failed exec and short-lived processes, and recover live registrations across service restarts. Reuse one runtime verification cycle for sessions sharing both policy and network context. Retain independent action hooks. Test simultaneous CLI and Desktop sessions, PID reuse, logout, and restart. Continuous polling remains best-effort process control.

## Ubuntu release acceptance

- Run the focused regressions above and `make check`. Record the revision, Ubuntu and Claude versions, and which tests exercised real components.
- In a disposable Ubuntu desktop account, exercise shell launch, menu actions, and URL activation with both CLI and Desktop open. Update the CLI with an old session alive, restart the user service, log out and back in, and suspend and resume.
- Exercise allowed and denied VPN transitions plus transient and sustained provider failure. Confirm an unrelated browser survives each denial. Verify all three hooks in real Claude. Use the existing [desktop acceptance checklist](../desktop-testing.md).
- Compare idle monitor CPU, requests per active minute, CLI and hook latency, and route-change-to-detection and termination times against the pre-change baseline. A multi-day run covering an actual update and a network transition remains outstanding.
