#!/usr/bin/env python3
"""Native-messaging host for the SonoScript Read Aloud extension.

Chrome/Edge only ever spawn this process on behalf of the extension ID listed in this host's
own manifest (com.sonoscript.bridge.json) — a web page has no way to reach this at all, which is
the entire point (see browser_bridge.py's docstring for the full trust chain this is one link
of). Deliberately has zero dependencies beyond the standard library: it hands back whatever token
SonoScript last wrote to disk, cold-launching the app first if it isn't already running, so it
never needs this app's much heavier ML dependency stack just to run.
"""
import json
import os
import socket
import struct
import subprocess
import sys
import time

TOKEN_PATH = os.path.expanduser("~/Library/Application Support/SonoScript/bridge_token")
# Must match browser_bridge.py's own PORT constant — this file can't import that module
# directly without pulling in the app's full dependency stack just to read one integer.
BRIDGE_PORT = 51823
LAUNCH_TIMEOUT_SECONDS = 20
LAUNCH_POLL_SECONDS = 0.25


def read_message():
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        return None
    length = struct.unpack("<I", raw_length)[0]
    return json.loads(sys.stdin.buffer.read(length))


def send_message(payload):
    encoded = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def _bridge_is_up():
    """Whether SonoScript's local listener is actually accepting connections right now — the
    real signal that the app is running, unlike TOKEN_PATH's mere existence: that file is never
    deleted when the app quits, so a stale token left over from a previous session would
    otherwise look identical to a currently-running one and this host would hand the extension
    a token nothing is listening for."""
    try:
        with socket.create_connection(("127.0.0.1", BRIDGE_PORT), timeout=0.3):
            return True
    except OSError:
        return False


def _launch_in_background():
    # `open -g` only stops macOS/Launch Services from ACTIVATING (focusing) the newly launched
    # app — it has no way to reach into the app itself and tell it not to show its own window.
    # `--args --browser-launch` is what actually does that: main.py checks sys.argv for exactly
    # this flag at startup and skips window.orderFront_ entirely on that path (see
    # BROWSER_LAUNCH and build_window() there) — stronger than the existing SONOSCRIPT_DEV_QUIET
    # env var, which still shows the window, just without stealing keyboard focus.
    try:
        subprocess.Popen(
            ["open", "-g", "-a", "SonoScript", "--args", "--browser-launch"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass  # surfaces as a timeout below either way — no separate error path needed


def _get_token():
    # _bridge_is_up() only confirms the HTTP listener's socket is accepting connections, which
    # per browser_bridge.py's start() happens a moment BEFORE it writes the token file (the
    # server socket binds/listens during construction; the token is written as the very next
    # line, not before). An already-running instance's token is always long since written by the
    # time this runs, but a freshly launched one could theoretically still be finishing that
    # write in the same instant the port becomes connectable — a handful of short retries closes
    # that narrow gap without adding meaningful latency to the overwhelmingly common
    # already-running case, which succeeds on the very first attempt.
    for attempt in range(10):
        try:
            with open(TOKEN_PATH) as f:
                return f.read().strip()
        except OSError:
            if attempt == 9:
                return None
            time.sleep(0.1)


def main():
    message = read_message()
    if message is None:
        return

    if not _bridge_is_up():
        _launch_in_background()
        deadline = time.monotonic() + LAUNCH_TIMEOUT_SECONDS
        while time.monotonic() < deadline and not _bridge_is_up():
            time.sleep(LAUNCH_POLL_SECONDS)
        if not _bridge_is_up():
            send_message({"error": "SonoScript didn't start in time."})
            return

    token = _get_token()
    if token is None:
        send_message({"error": "SonoScript doesn't appear to be running."})
        return
    send_message({"token": token})


if __name__ == "__main__":
    main()
