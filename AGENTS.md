# Releases

Use `v<version>` for both the Git tag and the GitHub release title. For example, publish version `0.2.0` with the tag and title `v0.2.0`.

# Testing safety

- Never test against the user's real Claude Desktop, Claude Code, their installed files or their running sessions. Do not send signals to real Claude processes, freeze their scopes or inspect their traffic.
- Use disposable stand-ins instead: `tests/fake_desktop.py` mimics Desktop's process roles (main, renderer, GPU, network service, embedded Claude Code, a detached crash helper) with copies of the Python interpreter under Claude-like names. Senders talk only to a localhost receiver. Add similar fakes for new scenarios, including the CLI.
- Keep an unrelated control process alive in containment tests and assert it survives.
- Clean up every disposable process and systemd scope a test creates.

# User experience

- Disrupt the user only when the threat is real: a confirmed country, IP or policy failure. Offline, slow or unreachable networks must not pause or close running apps.
- Prefer the least disruptive response that still stops traffic: pause network-capable processes and keep the window usable. Pause the window only if it opens a connection itself, and close Claude only when a pause stays unresolved.
- Notify once per state change. Never show contradictory or repeated messages.

# Guard design

- The country and IP check is the main guard. Routing events only make the next check happen sooner. Never resume or allow anything because routes changed back; resume only after a passing check.

# Performance

- The guard runs all day on a laptop. Idle monitoring must stay cheap: no new polling, subprocesses or network requests per cycle without a strong reason.
- Prefer kernel events (such as `ip monitor`) to polling. Extra scanning is acceptable only while a threat is active, and must stop when it ends.
- Measure the cost of new periodic work, and keep the measurement as a test when practical.
