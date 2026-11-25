"""A disposable stand-in for Claude Desktop's process tree. Never run the real application in tests.

Roles mirror Electron: a main process, a renderer that only draws (heartbeat file), a GPU
process, the network service that sends traffic, an embedded Claude Code child, and a crash
handler that detaches from the tree. Senders emit tagged UDP datagrams to a local receiver.
"""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

PORT = int(os.environ["FAKE_PORT"])
STATE = Path(os.environ["FAKE_STATE"])
ROLE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--role=")), None)
TYPE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--type=")), None)


def send_forever(tag, interval=0.01):
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    while True:
        sender.sendto(tag.encode(), ("127.0.0.1", PORT))
        time.sleep(interval)


def child(executable, *args, **kwargs):
    return subprocess.Popen([executable, "-I", __file__, *args], **kwargs)


if ROLE == "crashpad":
    # Double fork into a new session, like a crash handler that outlives its launcher.
    if os.fork():
        sys.exit(0)
    os.setsid()
    if os.fork():
        os._exit(0)
    (STATE / "crashpad.pid").write_text(str(os.getpid()))
    send_forever("crashpad")
elif ROLE == "code":
    send_forever("code")
elif ROLE == "orphan":
    send_forever("orphan")
elif TYPE == "utility":
    send_forever("network")
elif TYPE == "renderer":
    beats = 0
    while True:
        beats += 1
        # Readers must see a complete beat, never the empty file between truncate and write.
        pending = STATE / "heartbeat.tmp"
        pending.write_text(str(beats))
        pending.replace(STATE / "heartbeat")
        time.sleep(0.02)
elif TYPE == "gpu-process":
    while True:
        time.sleep(1)
else:
    desktop = sys.executable

    def on_term(*_):
        # Electron-like cleanup that starts new work while shutting down.
        child(desktop, "--role=orphan", start_new_session=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_term)
    child(desktop, "--type=renderer")
    child(desktop, "--type=gpu-process")
    child(desktop, "--type=utility", "--utility-sub-type=network.mojom.NetworkService")
    child(os.environ["FAKE_CODE"], "--role=code")
    child(os.environ["FAKE_CRASHPAD"], "--role=crashpad").wait()
    connection = None
    while True:
        if connection is None and (STATE / "connect").exists():
            connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            connection.connect(("127.0.0.1", PORT))
        if connection is not None:
            connection.send(b"main")
        time.sleep(0.05)
