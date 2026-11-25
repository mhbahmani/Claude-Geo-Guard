"""Show launch verification in progress without giving the dialog control of the check."""

import subprocess
import sys
from contextlib import contextmanager

from .platforms import backend

MESSAGE = "Checking your public IP and country before opening Claude…"


@contextmanager
def verifying(*, terminal=False):
    if terminal:
        print(MESSAGE, file=sys.stderr, flush=True)
    command = (
        backend("dialogs").progress_command(MESSAGE)
        if not terminal or (sys.stdin.isatty() and sys.stderr.isatty())
        else None
    )
    window = None
    if command:
        try:
            window = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass
    try:
        yield
    finally:
        if window is not None:
            with window:
                window.terminate()
                try:
                    window.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    window.kill()
