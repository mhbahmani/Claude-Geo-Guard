"""Repository bootstrap: no pip dependency and no reliance on PYTHONPATH."""

import sys
from pathlib import Path

if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ required")
sys.path.insert(0, str(Path(__file__).parent / "src"))
from claude_geo_guard.cli import main  # noqa: E402

raise SystemExit(main())
