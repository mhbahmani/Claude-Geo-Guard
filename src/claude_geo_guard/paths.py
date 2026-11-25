import os
import sys
from dataclasses import dataclass
from pathlib import Path

_installed = None


@dataclass(frozen=True)
class Paths:
    home: Path
    data: Path
    config: Path
    state: Path
    # Set by the installed bootstrap: the caller's environment no longer selects files.
    installed: bool = False

    @classmethod
    def current(cls):
        if _installed is not None:
            return _installed
        home = Path.home()
        if sys.platform == "darwin":
            support = home / "Library/Application Support"
            return cls(home, support, support, support / "claude-geo-guard/state")

        def xdg(name, fallback):
            value = Path(os.environ.get(name) or fallback)
            return value if value.is_absolute() else Path(fallback)

        return cls(
            home,
            xdg("XDG_DATA_HOME", home / ".local/share"),
            xdg("XDG_CONFIG_HOME", home / ".config"),
            xdg("XDG_STATE_HOME", home / ".local/state"),
        )

    @classmethod
    def pin(cls, home, data, config, state):
        global _installed
        _installed = cls(Path(home), Path(data), Path(config), Path(state), installed=True)
        return _installed

    @property
    def root(self):
        root = self.data / "claude-geo-guard"
        return root / "runtime" if sys.platform == "darwin" else root

    @property
    def bin(self):
        return (
            (self.data / "claude-geo-guard/bin") if sys.platform == "darwin" else self.root / "bin"
        )

    @property
    def manifest(self):
        if sys.platform == "darwin":
            return self.state / "manifest.json"
        return self.state / "claude-geo-guard/manifest.json"

    @property
    def guard_config(self):
        return self.config / "claude-geo-guard/config.json"

    @property
    def settings(self):
        return self.home / ".claude/settings.json"

    @property
    def monitor_health(self):
        return self.manifest.parent / "monitor-health.json"
