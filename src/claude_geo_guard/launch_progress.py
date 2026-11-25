"""Show launch verification in progress without giving the dialog control of the check."""

import subprocess
import sys
import threading
from contextlib import contextmanager

from .platforms import backend

MESSAGE = "Checking your public IP and country before opening Claude…"


@contextmanager
def terminal_progress():
    stream = sys.stderr
    if not stream.isatty():
        print(MESSAGE, file=stream, flush=True)
        yield
        return

    label = MESSAGE.removesuffix("…")
    print(label, end="", file=stream, flush=True)
    stop = threading.Event()

    def animate():
        dots = 1
        while not stop.wait(0.3):
            print(f"\r{label}{'.' * dots:<3}", end="", file=stream, flush=True)
            dots = (dots + 1) % 4

    worker = threading.Thread(target=animate, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join()
        print(file=stream, flush=True)


@contextmanager
def verifying():
    command = backend("dialogs").progress_command(MESSAGE)
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
