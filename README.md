# Claude Geo Guard

Check the public country and IP before opening Claude Code or Claude Desktop on macOS and Linux. If verification fails, the guard blocks the launch. A background monitor periodically checks running Desktop processes.

## Install

With Git, Python 3.10+ and Claude Code 2.1.295+ installed, run this in a terminal as your regular user:

```sh
git clone https://github.com/mhbahmani/Claude-Geo-Guard.git && cd Claude-Geo-Guard && ./install.sh
```

The installer previews its changes and asks for confirmation. It installs the **latest release** and defaults to **GB**. To select another country, add `--country DE` (or your country code) to the install command. Use `--skip-desktop` for CLI and hooks only, or `--dev` for master.

## After installation

- **macOS:** Open `~/Applications/Claude Guard.app` and pin it to the Dock. Opening the original Claude.app bypasses the pre-launch check.
- **Ubuntu or Debian:** Open Claude from its guarded applications-menu entry.
- **CLI:** Open a new terminal and run `claude-geo-guard check` to verify your connection. Then use `claude` as usual.

Traffic can occur between monitor checks. To remove the guard, run `claude-geo-guard uninstall`.

[Installation and prerequisites](docs/installation.md) · [Configuration and usage](docs/usage.md) · [Troubleshooting](docs/troubleshooting.md) · [Security model](docs/security-model.md) · [Releases](https://github.com/mhbahmani/Claude-Geo-Guard/releases)
