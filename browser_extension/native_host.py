#!/usr/bin/env python3
"""Native-messaging host for the SonoScript Read Aloud extension.

Chrome/Edge only ever spawn this process on behalf of the extension ID listed in this host's
own manifest (com.sonoscript.bridge.json) — a web page has no way to reach this at all, which is
the entire point (see browser_bridge.py's docstring for the full trust chain this is one link
of). Deliberately has zero dependencies beyond the standard library: it does one small job (hand
back whatever token SonoScript last wrote to disk) and nothing else, so it never needs this
app's much heavier ML dependency stack just to run.
"""
import json
import os
import struct
import sys

TOKEN_PATH = os.path.expanduser("~/Library/Application Support/SonoScript/bridge_token")


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


def main():
    message = read_message()
    if message is None:
        return
    try:
        with open(TOKEN_PATH) as f:
            token = f.read().strip()
        send_message({"token": token})
    except OSError:
        # SonoScript hasn't been launched yet this session (or ever) — no token file to
        # read, not a crash. The extension side already handles a missing "token" field.
        send_message({"error": "SonoScript doesn't appear to be running."})


if __name__ == "__main__":
    main()
