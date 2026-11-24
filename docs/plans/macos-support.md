# macOS support and publication readiness plan

Status: experimental macOS CLI/hooks implemented. Desktop launcher and LaunchAgent implemented. Real Claude and GUI acceptance remain pending. Prepared 2026-11-24 from the current repository.

This is a handoff for a future implementation session. The current product supports Linux only. This document authorizes no publication, service changes, installation into a real user home, or changes to Anthropic's application. Implement the plan when the user asks to continue it.

## 1. Objective and decisions

Add macOS support to **claude-geo-guard** while preserving the working Linux implementation. Keep one repository, Python package, command, configuration schema, installation workflow, and uninstall workflow. Separate operating-system integration behind small, explicit interfaces.

- Keep `install.sh` and `uninstall.sh` as the public entry points on both systems. Dispatch by operating system in Python, not by maintaining two large shell installers.
- Keep Linux defaults and installed paths unchanged. Do not add migration code, old project-name aliases, or a legacy-settings fallback.
- Keep the default branch `master`. Do not rename the local checkout as part of the refactor; the proposed GitHub repository name is `claude-geo-guard`.
- Add `make install`, `make uninstall`, `make install-dry-run`, and `make help` as conveniences that invoke the same scripts. Never make installation depend on the development virtual environment.
- Target Apple Silicon and Intel Macs, but publish architecture/version support only after testing it. Anthropic currently lists macOS 13+; this is a candidate minimum, not evidence that the guard supports every such release. Recheck before implementation. [Anthropic system requirements](https://support.claude.com/en/articles/10065433-install-claude-desktop)
- Use a user LaunchAgent for the monitor, and a separate **Claude Guard.app** launcher. Leave the signed Claude.app bundle untouched.
- Keep best-effort process control as the enforcement model. No firewall rules, Network Extensions, root daemon, Gatekeeper bypass, or strict network-enforcement claims in this phase.
- Keep country/IP policy and hooks in Python. Prefer a small native macOS helper for application events, native UI and launch environment handling; validate that choice in the first Mac prototype before expanding it.

## 2. Current baseline and constraints

Read these files before editing:

| Current file | Relevant coupling or responsibility |
| --- | --- |
| `install.sh`, `uninstall.sh`, `run.py` | Thin shell entry points and source-tree Python bootstrap; reusable. |
| `src/claude_geo_guard/installer.py` | Linux-only preflight, package installation, transaction records, hooks, desktop overrides, systemd and shell setup. |
| `src/claude_geo_guard/checker.py` | Shared policy/provider logic, but imports Linux route snapshots directly. |
| `src/claude_geo_guard/paths.py` | Linux XDG path defaults. |
| `src/claude_geo_guard/desktop_launcher.py` | Guarded launch, Linux service check and UI dependency. |
| `src/claude_geo_guard/monitor.py` | Reusable checking loop, but imports Linux dialogs and process operations. |
| `src/claude_geo_guard/process_manager.py` | Linux `/proc`, start times and pidfds; must remain Linux-specific. |
| `src/claude_geo_guard/cli.py` | Commands plus Linux-specific diagnostics; separate diagnostics from command parsing. |
| `src/claude_geo_guard/linux/` | Desktop entries, networking, systemd, dialogs, shell editing. Shell editing is mostly reusable POSIX logic. |
| `src/claude_geo_guard/config.py`, `settings_manager.py`, `storage.py` | Preserve strict validation, non-destructive hook ownership, atomic writes, backups and transactions. Audit filesystem assumptions on macOS. |

Recorded baseline: `make check` passed **303 tests** on Ubuntu 24.04.5/Python 3.12.3; wheel and sdist passed `twine check`. These are previous results, not validation of any future refactor. See [validation record](../validation.md). Real desktop/hook behavior and hosted CI have separate verification gaps.

At planning time `git status --short` lists the project as untracked and the branch is `master`. Do not bulk stage, commit, delete generated files, or assume ownership of existing changes. The user must explicitly request a commit or publication.

### Preserve the user's current behavior

The editable config has these settings:

```json
{
  "allowed_country": "GB",
  "timezone": "Europe/London",
  "interval": 15,
  "grace": 5,
  "allowed_ips": [],
  "success_notifications": true
}
```

- `allowed_ips` is an optional exact-IP allowlist, not a country override. Every usable address family must pass both policies when enabled.
- Use `https://ifconfig.io/all.json`: country and public IP come from the same request. Preserve strict response validation, TLS validation, timeouts, route-change detection, proxy handling and fail-closed outcomes.
- Both successful and failed launch messages show returned country and IP, even with IP matching disabled. Missing observations are explicitly unavailable; never invent or reuse a stale IP.
- Success confirmations occur **only on guarded Desktop launches**. The monitor never displays success/recovery pop-ups, launches Claude, or restarts Claude.
- Runtime violations terminate positively identified protected processes and display an error window. UI failure must not disable checking or termination.
- Observed public IPs are not automatically written to persistent monitoring logs. Configured IPs and notification history have separately documented privacy implications.
- Invalid config denies checks. Monitor configuration is reloaded; timezone applies only to newly launched processes, not the host timezone or already-running Claude.
- Preserve all unrelated settings/hooks and user edits. Keep Base64 restoration records internal, separate from editable config.

## 3. Proposed repository structure

Keep shared modules relatively flat. Do not build a plugin framework or duplicate the whole application under each OS. The following is the target structure; existing docs/tests remain unless deliberately consolidated with links updated.

```text
claude-geo-guard/
├── README.md
├── LICENSE
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
├── pyproject.toml
├── requirements-dev.txt
├── MANIFEST.in
├── Makefile
├── install.sh
├── uninstall.sh
├── run.py
├── src/claude_geo_guard/
│   ├── __init__.py
│   ├── cli.py
│   ├── diagnostics.py
│   ├── checker.py
│   ├── config.py
│   ├── policy.py
│   ├── timezone.py
│   ├── paths.py
│   ├── installer.py
│   ├── settings_manager.py
│   ├── storage.py
│   ├── shell.py
│   ├── messages.py
│   ├── cli_launcher.py
│   ├── desktop_launcher.py
│   ├── monitor.py
│   ├── platforms/
│   │   ├── __init__.py          # lazy backend selection; unsupported OS fails clearly
│   │   ├── contracts.py         # small protocols and shared result types
│   │   ├── linux/
│   │   │   ├── __init__.py
│   │   │   ├── environment.py
│   │   │   ├── desktop_entries.py
│   │   │   ├── networking.py
│   │   │   ├── processes.py
│   │   │   ├── service.py       # systemd implementation
│   │   │   └── dialogs.py
│   │   └── macos/
│   │       ├── __init__.py
│   │       ├── environment.py
│   │       ├── applications.py
│   │       ├── networking.py
│   │       ├── processes.py
│   │       ├── service.py       # launchd implementation
│   │       └── dialogs.py
│   └── resources/
│       ├── config.example.json
│       ├── linux/claude-geo-guard.service
│       └── macos/
│           ├── launch-agent.plist
│           └── Info.plist
├── native/macos/                # only if native-helper prototype passes
│   ├── Package.swift
│   ├── Sources/...
│   └── Tests/...
├── tests/
│   ├── conftest.py
│   ├── unit/                   # config, checker, hooks, transactions, orchestration
│   ├── platforms/linux/
│   ├── platforms/macos/
│   ├── integration/linux/
│   ├── integration/macos/
│   └── fixtures/               # synthetic bundles, entries, command outputs
├── docs/
│   ├── index.md
│   ├── architecture.md
│   ├── security-model.md
│   ├── configuration.md
│   ├── installation.md
│   ├── troubleshooting.md
│   ├── validation.md
│   ├── platforms/linux.md
│   ├── platforms/macos.md
│   ├── testing/linux-desktop.md
│   ├── testing/macos-desktop.md
│   └── plans/macos-support.md
└── .github/
    ├── workflows/tests.yml
    ├── ISSUE_TEMPLATE/bug_report.yml
    └── ISSUE_TEMPLATE/feature_request.yml
```

Move resources into the Python package and load them through `importlib.resources`; test availability from both wheel and sdist outside the source checkout. Keep one canonical template per resource. If native artifacts are distributed later, choose architecture-aware artifacts/tags instead of hiding binaries in a falsely platform-independent wheel.

### Backend boundaries

Use explicit dependency injection in tests and lazy OS selection in production. Shared modules must import successfully on either OS without invoking platform commands at import time.

| Boundary | Contract |
| --- | --- |
| Environment/paths | Platform identity, supported capabilities, prerequisites, writable installation paths. |
| Networking | Usable address families and a stable network-state fingerprint; uncertainty is an error, not an absent family. |
| Desktop integration | Discover candidates, plan managed launcher files, launch a selected verified application, diagnose coverage. |
| Service | Inspect loaded/enabled/running state separately; install/start/stop/uninstall with recoverable prior state. |
| Processes | Inspect identity, enumerate owned application processes/descendants, terminate with documented identity safeguards. |
| UI | Render shared message content with native presentation; launch success is separate from failure windows. |

Keep installer transaction order, monitor scheduling, hook merging, CLI exit codes and verification policy shared. Do not create generic interfaces for functions that are already portable.

## 4. One installer and predictable paths

Supported entry points after implementation:

```sh
./install.sh --dry-run
./install.sh --yes --country GB --timezone Europe/London
./install.sh --status
./install.sh --check
./uninstall.sh
make install
make install-dry-run
make uninstall
```

`make install` remains interactive by default; support `INSTALL_ARGS='--yes --country GB'` explicitly. It must invoke the same installer, not `pip install` as an alternative installation mechanism. Add `--desktop-app /absolute/path/Claude.app` for unambiguous Mac selection. Preserve existing flags and semantics, including explicit `--skip-desktop` for CLI/hooks-only operation.

Installer sequence:

1. Resolve a suitable Python 3.10+ interpreter and select the Linux/Darwin backend. Reject unsupported OS before filesystem mutations. Never assume macOS ships a usable Python or silently install Homebrew/Xcode.
2. Inspect GUI session, shell, dependencies, Claude app candidates, CLI/version, settings, current installation and service state. On Mac, account for Apple Silicon/Intel and Homebrew locations without hardcoding one installation path.
3. Validate all requested capabilities before writing. Missing Desktop prerequisites fail clearly; CLI-only installation requires explicit selection.
4. Show exact files, service actions, app selection, policy and protection gaps. Dry-run creates no package, cache, backup, build output, service or GUI window.
5. Acquire the installation lock, revalidate inputs, back up originals, then install the private package, wrappers, config, owned hooks and platform launcher/service files transactionally.
6. Activate the monitor only after its complete configuration is present. Desktop launch checks require a healthy monitor. Explain that enabling it can close an already-running protected app when verification fails.
7. Verify parsed files, ownership, interpreter availability, hooks, service state and executable launch paths. Report functional desktop/hook behavior as unverified until tested.
8. On failure, undo only this attempt's changes and restore previous service state; never restore an old settings snapshot over concurrent user edits.

Proposed Mac locations (Linux stays unchanged):

| Item | macOS location |
| --- | --- |
| Editable config | `~/Library/Application Support/claude-geo-guard/config.json` |
| Private package and wrappers | `~/Library/Application Support/claude-geo-guard/runtime/` and `bin/` |
| Internal manifest/backups | `~/Library/Application Support/claude-geo-guard/state/` |
| Guarded application | `~/Applications/Claude Guard.app` |
| Background job | `~/Library/LaunchAgents/local.claude-geo-guard.monitor.plist` |
| Bounded private logs | `~/Library/Logs/claude-geo-guard/` |
| Claude Code hooks | `~/.claude/settings.json` |

Use the same `Paths` interface; `doctor` prints resolved locations. Do not invent a publisher-owned domain in the bundle/job identifier; use a clearly local identifier until a real maintainer chooses a namespace. Respect spaces, Unicode, symlinks/conflicts, file modes and ZDOTDIR. Keep the current refusal of unsupported `CLAUDE_CONFIG_DIR` until support is deliberately implemented and tested.

Uninstall must boot out the owned agent, remove only owned unchanged files/hooks/PATH blocks, preserve user edits/config as today, and retain backups. Never remove Claude.app, unrelated agents, or restore global Dock/association snapshots. Reinstallation must not duplicate any integration.

## 5. macOS implementation details and acceptance gates

### A. Native Desktop launcher and dialogs

First prototype a small Swift/AppKit helper with a JSON/argument protocol to the Python guard. It handles application-open events and native UI; Python remains the sole policy engine. Use explicit executable paths and structured data, never interpolate provider text into AppleScript or a shell.

The candidate launch API is `NSWorkspace` with `OpenConfiguration`; validate environment injection, argument forwarding, URLs/files and existing-app behavior on a real Mac. Do not assume `open -a` preserves the guard's timezone or shell environment. [Apple launch API](https://developer.apple.com/documentation/appkit/nsworkspace/openapplication(at:configuration:completionhandler:)), [launch configuration](https://developer.apple.com/documentation/appkit/nsworkspace/openconfiguration)

- Discover installed apps in standard Applications locations and an explicit user path; read `Info.plist`, validate bundle identity and executable existence, and exclude the guard itself. Spotlight search may supplement discovery, not be the sole source.
- Multiple candidates require explicit selection or a clear plan choice. No arbitrary first match. Revalidate after updates/moves; unexpected replacement or identity changes deny launch pending review/reinstall.
- Install a visibly distinct **Claude Guard.app**, with a project-owned icon and distinct bundle ID. Do not copy Anthropic branding into a misleading replacement or modify its signature.
- Every open/reopen/file/URL event handled by the guarded app must check policy before forwarding. Keep the UI responsive while Python checks, with a bounded timeout that denies on failure.
- Existing Claude instances may be activated after a successful check, but cannot acquire a new TZ value. Clearly require fully quitting and reopening for timezone changes.
- Failure windows show a short reason, configured policy, returned country/IP, and optional diagnostic details. Success confirmations are brief and launch-only, obeying the setting. Notification permission/Do Not Disturb must never affect enforcement.
- Runtime alerts must not block the checking loop, accumulate duplicate windows, or survive monitor shutdown indefinitely. Closing a dialog does not authorize access.
- Do not silently replace Dock favorites, register as a default URL/file handler, or change login items. Document pinning the guarded app. Show discovered original launch paths as gaps in diagnostics.
- First Mac release may leave URL/default-handler interception unsupported. It must still safely handle file/URL events deliberately sent to the guarded app if claiming that feature; otherwise reject them visibly rather than dropping arguments or launching unchecked.

**Delivery gate:** source installations using Swift require Command Line Tools, checked before mutations. Do not launch an automatic toolchain download. If this requirement is unacceptable, stop and record the tradeoff before choosing a different helper. Signed/notarized prebuilt distribution is a separate release task requiring real signing credentials; never disable Gatekeeper or strip quarantine automatically. Test downloaded-archive and locally built behavior independently.

### B. LaunchAgent runtime service

Use the logged-in user's GUI domain and a per-user plist. Apple documents user agents for session-specific background processes. [Apple launchd guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)

- Serialize with `plistlib`, absolute `ProgramArguments`, a stable interpreter path and narrowly scoped environment. No shell expansion or dependence on interactive PATH.
- Validate modern `launchctl bootstrap`, `bootout`, `print` and `kickstart` behavior using the target Mac's manuals and test account. Handle already loaded/unloaded jobs idempotently; do not hide permission/session errors.
- Start the monitor at login and recover monitor crashes with throttling; never configure launchd to start/restart Claude. Avoid rapid restart loops on invalid config or provider outages.
- Differentiate a loaded plist from a live healthy monitor. Bound any health acknowledgment and do not trust stale state as proof of protection.
- Recheck after sleep/wake and network changes; avoid persisting an allow verdict across sleep. Validate scheduling so an elapsed wait triggers a fresh check promptly. No recovery pop-ups.
- Write bounded, rotated, owner-readable structured logs with reason/family/provider/error class and termination outcome, excluding observed IPs and secrets. Surface exact log location in failure details and `doctor`.
- Uninstall/rollback must restore the prior agent state where safe. Verify logout/login, crash restart, shutdown, UI delivery and reinstall in an actual GUI session.

### C. Networking and process safety

Networking prototype: use native route/interface information (or narrowly parsed `/sbin/route`, `/sbin/ifconfig` and system proxy inspection) to implement the existing family/fingerprint contract. Validate parser fixtures and real outputs before settling the backend. Inspect IPv4/IPv6, scoped routes, VPN `utun` interfaces, source selection, interface state and proxy/PAC settings. Do not treat a present interface as a usable route, or an ambiguous lookup as an unavailable family.

Keep per-family HTTPS provider checks and before/after snapshots. Detect system proxy ambiguity as well as environment proxies; do not silently start following a PAC file. Document split-tunnel and per-destination routing gaps: ifconfig.io egress may differ from Claude's egress. Test Wi-Fi/Ethernet switching, VPN changes, IPv6-only/NAT64 conditions, sleep/wake, DNS/TLS/provider failures and network changes during verification. Unsupported or ambiguous cases deny rather than silently skip a family.

Process prototype: evaluate native process APIs for UID, executable path, start identity, parent identity and application bundle membership. A macOS-only dependency is acceptable if it materially improves correctness and is declared with a platform marker, but avoid unsafe guessed `ctypes` layouts. Do not use process-name matching, `pkill`, shell command-line substrings, or broad process-group kills.

Retain Linux pidfd protection unchanged. **macOS does not gain pidfd semantics by checking a PID/start time and then calling kill.** Revalidate identity immediately before any signal, test PID reuse, and explicitly assess the remaining inspection-to-signal race. If safe targeted forced termination cannot be established, do not claim equivalent guarantees: document the precise limitation, refuse unsafe targets, and keep full Desktop support experimental until the decision is reviewed. Prefer verified application termination APIs where appropriate; prove helper/child handling separately.

Track only same-user, positively identified app processes and proven descendants; account for reparenting, helper apps, app updates and processes exiting mid-check. An unrelated process called Claude must survive. Do not terminate the guard itself or another user's processes. Runtime diagnostics must report inability to identify/terminate protected processes as degraded protection, not success.

### D. CLI, hooks and timezone

Reuse the CLI exec wrapper and non-destructive settings merger. Test native executable replacement, symlinks, spaces, PATH recursion, shell login/non-login precedence, argument forwarding, signals and exit codes on both Mac architectures. Finder/launchd must not rely on shell initialization files for discovering Python or Claude.

Revalidate the required Claude Code hook features and minimum version at implementation time; the current code gates on 2.1.295. Test `UserPromptSubmit`, `UserPromptExpansion`, `PreToolUse` with `*`, explicit exec arguments, and `onFailure: "block"`, including missing executable, timeout and invalid output. Preserve unrelated handlers and order. [Hook reference](https://code.claude.com/docs/en/hooks)

Verify actual local Desktop Code hook execution independently of CLI tests; bundled executable/version and settings resolution may differ. Remote/SSH/cloud sessions remain outside the local-device guarantee. [Desktop documentation](https://code.claude.com/docs/en/desktop)

Pass TZ only into new guarded processes, including launches through the native helper. Test daylight-saving behavior and real Claude display separately: an application may choose OS/native timezone APIs instead of honoring TZ. Never change the machine timezone or promise application-wide timezone behavior based solely on an environment test.

## 6. Implementation phases

Execute in order; keep each phase independently reviewable. Do not make commits unless requested.

1. **Baseline and repository organization.** Inspect worktree instructions/status; rerun existing checks. Add documentation navigation and classify existing tests. Record current Linux behavior and verification gaps. Avoid unrelated formatting or a broad rewrite.
2. **Extract platform boundaries.** Move existing Linux code into `platforms/linux`, introduce lazy backend selection and shared message models, extract diagnostics, and update imports/tests. Preserve Linux config paths, installed command paths, ownership records and behavior without migration shims. Run the complete Linux suite and package tests before adding Mac behavior.
3. **Mac feasibility prototypes.** On a real Mac, validate native app launch/TZ/events, safe process identity/termination, dual-stack route snapshots, native UI and LaunchAgent lifecycle. Write the chosen APIs, dependencies and remaining races into the plan. These are release gates, not assumptions to hide with mocks.
4. **Portable installer and CLI/hooks.** Implement Mac paths/preflight, shared script dispatch, Make conveniences, private-runtime packaging and rollback. Prove explicit CLI-only installation in isolated test homes; don't claim Desktop protection yet.
5. **Mac Desktop and monitor.** Implement the selected helper, discovery, service, process backend, failure UI and launch-only success confirmation. Require monitor health before guarded app launch. Exercise failure and uninstall paths as much as success paths.
6. **Diagnostics and documentation.** Report platform-specific capabilities and gaps; add Mac setup/recovery/manual testing docs. Update metadata/support matrix only to the level demonstrated by evidence.
7. **CI and publication review.** Run both OS suites, artifacts and clean-checkout checks, then the real desktop acceptance checklist. Leave the repository ready for review; publishing, tagging, signing credentials and pushing require separate user instruction.

## 7. Test and release acceptance matrix

| Area | Required evidence |
| --- | --- |
| Shared policy | Existing country/IP/config failure cases run on Linux and macOS, with mock networking. |
| Import isolation | macOS never requires `/proc`, pidfds, `ip`, systemd or Linux dialogs; Linux never imports AppKit/native Mac libraries. Unsupported OS fails before writes. |
| Installer | Both scripts and Make targets, fresh/repeat install, dry-run no writes, absent Python/dependencies, non-ASCII/space paths, invalid config, edited files, partial failure, rollback, repeat uninstall. |
| Preservation | Existing hook order/content, concurrent settings edits, user config, shell blocks, unrelated files and original Claude app unchanged. |
| Packaged operation | Install/test from clean extracted sdist and installed wheel outside checkout; resources present; no developer absolute paths or hidden build-time dependency. |
| Desktop | Synthetic app bundles, multiple candidates, moved/replaced app, malformed plist, missing executable, arguments/URLs/files, already-running app, environment/TZ, launch denied on missing monitor. |
| Monitoring | No app means no provider polling; UK-to-denied/failure, recovery without launch/pop-up, shutdown, alert deduplication, crash recovery, sleep/wake, configuration reload. |
| Process safety | Same-user exact identity, helpers/descendants, exits during checks, stale/reused PID, name collisions, graceful/forced outcomes and explicit unresolved race coverage. |
| Real Mac | Finder/Spotlight/Dock guarded launch, native dialogs/notifications, permissions, local Desktop Chat/Code, all hook checkpoints, VPN transition, original-app bypass, reinstall/uninstall, reboot/login. |

Unit/integration tests use temporary HOME and platform paths, fake providers/service managers, synthetic apps and isolated processes. No real user hooks, launch agents, login items, Claude processes or configuration may be touched by normal tests. Native service lifecycle tests require a dedicated test account or disposable Mac environment and explicit opt-in; a temporary HOME alone does not isolate `launchctl`'s real user domain.

Keep Ubuntu/Python and Debian container coverage. Add pinned Mac runner labels for Apple Silicon and Intel plus relevant Python versions. At planning time GitHub documents `macos-15` (arm64) and `macos-15-intel`; recheck availability before editing CI. Hosted runners do not prove GUI/session behavior or macOS 13 compatibility. [GitHub runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)

Run ShellCheck, Python lint/format/unit/integration checks, Linux desktop validation, Mac plist/native helper checks, wheel/sdist build and artifact validation. Use job timeouts, least-privilege workflow permissions and pinned action revisions with an intentional update process. No normal CI job should require Claude credentials, a real installation, or a real ifconfig.io response. Keep live smoke checks manual and avoid uploading public-IP verdicts by default.

## 8. GitHub publication cleanup

- README: concise purpose, honest support matrix, one quick start per OS, configuration example, uninstall, diagram and links to detailed docs. Avoid duplicating the full config reference and test record.
- Add `docs/index.md`; split Linux/Mac specifics and manual acceptance from common architecture/security/configuration. Update all moved links. Keep this handoff under `docs/plans/` rather than presenting future capabilities as current features.
- Keep the Apache 2.0 license, no invented author/organization, no fake badges or passing-CI claims. Use project-owned artwork and accurate descriptions of vendor trademarks.
- Audit version/name consistency, console entry point, classifiers, requirements and package contents. Advertise macOS only after passing its gates; mark partial capabilities experimental explicitly.
- Audit tracked/release files for real usernames, home paths, configured IPs, tokens, logs, backup manifests and provider responses. Use synthetic fixtures and placeholder paths in public documentation. Preserve the user's local files; remove sensitive content from release inputs rather than blindly deleting files.
- Keep `.venv`, caches, `dist`, `build`, egg-info, native build outputs, `.DS_Store`, local logs and installation artifacts out of version control. Do not include generated binaries in source unless intentionally released with provenance.
- Use one canonical resource copy and reproducible build commands. Audit both wheel and sdist contents, not only metadata validation. Avoid stale artifacts being mistaken for the newly built release.
- Add focused issue templates requesting OS/architecture/version and redacted diagnostics. Security reporting must use a real configured channel; do not invent a maintainer email. A PR template is unnecessary unless requested; leave MR descriptions empty under the user's workflow.
- CONTRIBUTING: clean environment setup, test isolation, OS-specific testing, support claims, code layout and manual acceptance. CHANGELOG: describe user-facing additions, not internal conversation history.
- Record actual commands/results and missing validations in `docs/validation.md`. A CI definition is not evidence that hosted CI passed.
- Keep `master`; do not push, create a remote repository, tag or publish packages as part of this plan.

## 9. Definition of done and remaining decisions

macOS support is ready to advertise only when the same installer can perform a reversible user-level installation, guarded Desktop/CLI launches enforce checks, the agent closes positively identified processes on violations, hooks execute as required, returned country/IP is visible, and all Linux regression tests still pass. Native acceptance must include a real Mac and record which OS versions and CPU architectures were tested.

Resolve these explicitly during phase 3:

1. Minimum supported macOS version and architecture test coverage.
2. Native-helper build requirement versus later signed binary distribution.
3. Exact process identity/termination APIs and their residual race limitations.
4. LaunchServices environment, file/URL and already-running app behavior.
5. Native route/proxy APIs and supported VPN/IPv6-only cases.
6. Which launch associations remain unsupported in the first release.

If any gate cannot be met safely, deliver verified CLI/hooks-only capability or an explicitly experimental Desktop mode; do not label missing protection as installed-and-working. No Windows work, firewall isolation, remote-host provisioning, migration layer or publication automation is included.

## 10. Resume prompt for the next session

> Read `docs/plans/macos-support.md`, repository instructions and `docs/validation.md`. Inspect the worktree and preserve existing changes. Implement the plan in phases, starting with the Linux baseline and platform separation. Use the same install/uninstall scripts and keep master. Preserve editable country/IP/timezone config, all user hooks, launch-only success messages and runtime failure windows. Do not add migration code or install into my real home during development. Do not push or publish. Report Mac capabilities as unverified until tested on a Mac; if no Mac is available, complete portable refactoring and mocked tests, then record the native validation blockers honestly.
