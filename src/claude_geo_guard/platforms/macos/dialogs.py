import subprocess


def available():
    return "osascript"


def present(script, message, stop=None):
    try:
        with subprocess.Popen(
            ["/usr/bin/osascript", "-e", "on run argv", "-e", script, "-e", "end run", message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ) as proc:
            while True:
                try:
                    return proc.wait(timeout=0.25) == 0
                except subprocess.TimeoutExpired:
                    if stop and stop.is_set():
                        proc.kill()
                        return False
    except OSError:
        return False


def show_error(error, runtime=False, stop=None):
    message = str(error)
    if runtime and error.verdict:
        message += "\n" + str(error.verdict.countries) + " " + str(error.verdict.ips)
    return present(
        'display alert "Claude Guard" message (item 1 of argv) as critical giving up after 60',
        message,
        stop,
    )


def notify(message):
    present('display notification (item 1 of argv) with title "Claude Guard"', message)
