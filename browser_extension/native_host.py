#!/usr/bin/env python3
"""Native-messaging host for the SonoScript Read Aloud extension.

Chrome/Edge only ever spawn this process on behalf of the extension ID listed in this host's
own manifest (com.sonoscript.bridge.json) — a web page has no way to reach this at all, which is
the entire point (see browser_bridge.py's docstring for the full trust chain this is one link
of). Deliberately has zero dependencies beyond the standard library.

Always responds within a fraction of a second — never blocks waiting for SonoScript to finish
cold-starting. An earlier version polled here for up to 20s before replying; confirmed directly
(via real testing, not just reasoning about the docs) that this doesn't actually work: Chrome's
native-messaging connections have a real practical timeout well under that, and the MV3 extension
service worker that's waiting on the callback can itself be suspended as idle while the call just
sits there, losing the whole request silently. The retry loop now lives in background.js instead
(see getTokenWithRetry there) — every call here is short, and {"launching": true} tells that loop
to ask again shortly rather than this process itself waiting around.
"""
import json
import os
import pwd
import socket
import struct
import subprocess
import sys
import time

# Resolves the real OS-registered home directory via the user database, bypassing the HOME
# environment variable entirely. Confirmed elsewhere in this file (see _launch_in_background()'s
# own comment) that Chrome hands this process a different, restricted environment than a normal
# Launch-Services-launched app gets — that's what caused a real Gatekeeper failure earlier. If
# HOME were ALSO different in that environment — not just unset, where os.path.expanduser's own
# pwd-database fallback would already cover it, but set to something else — this file and main.py
# (which DOES get a normal HOME, launched via Launch Services) could silently compute two
# different marker-file paths and never find each other, with no error raised anywhere. Resolving
# home the same OS-user-database way on both sides removes that possibility regardless of
# whatever environment variables either process happens to be handed.
_HOME = pwd.getpwuid(os.getuid()).pw_dir

TOKEN_PATH = os.path.join(_HOME, "Library/Application Support/SonoScript/bridge_token")
# A marker this host writes right before triggering a launch, so the NEXT short-lived retry
# (a few hundred ms to a couple seconds later, chrome.runtime.sendNativeMessage spawns a fresh
# process per call — there's no shared memory between one invocation of this script and the
# next) doesn't ALSO see "not running yet" and trigger a second, duplicate launch. A plain
# existence check isn't enough here either, same reasoning as TOKEN_PATH below — this one's
# staleness is judged by mtime instead, since a truly stuck/failed launch attempt should still
# be recoverable on a later request rather than wedged forever.
LAUNCH_MARKER_PATH = os.path.join(_HOME, "Library/Application Support/SonoScript/launching")
LAUNCH_MARKER_MAX_AGE_SECONDS = 25
# main.py deletes this on startup to learn "this launch came from the browser extension, stay
# invisible" — see the long comment on _launch_in_background() for why this replaced an argv
# flag. Same directory as the other marker files above, kept separate from LAUNCH_MARKER_PATH
# since that one is scoped to THIS host's own dedup logic and gets checked/written on every
# call, while this one is scoped to a single real launch and only main.py ever touches it after
# it's written here.
PENDING_BROWSER_LAUNCH_PATH = os.path.join(
    _HOME, "Library/Application Support/SonoScript/pending_browser_launch"
)
# Must match browser_bridge.py's own PORT constant — this file can't import that module
# directly without pulling in the app's full dependency stack just to read one integer.
BRIDGE_PORT = 51823
APP_NAME = "SonoScript"


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


def _launch_recently_triggered():
    try:
        return (time.time() - os.path.getmtime(LAUNCH_MARKER_PATH)) < LAUNCH_MARKER_MAX_AGE_SECONDS
    except OSError:
        return False


def _mark_launch_triggered():
    os.makedirs(os.path.dirname(LAUNCH_MARKER_PATH), exist_ok=True)
    with open(LAUNCH_MARKER_PATH, "w") as f:
        f.write(str(time.time()))


def _find_app_binary():
    """Resolves the real installed path of SonoScript.app via the same mechanism Launch
    Services/Finder use (so this works regardless of whether it's in /Applications, an
    /Applications subfolder, or anywhere else a real install could legitimately live — confirmed
    directly: on this very machine it's NOT at the /Applications/SonoScript.app path a naive
    hardcode would have assumed), then returns the path to its actual executable inside the
    bundle. Returns None if the app can't be found at all."""
    try:
        result = subprocess.run(
            ["osascript", "-e", f'POSIX path of (path to application "{APP_NAME}")'],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            return None
        bundle_path = result.stdout.strip()
        if not bundle_path:
            return None
        # CFBundleExecutable matches CFBundleName ("SonoScript") — confirmed directly against
        # the real built bundle's Info.plist, not assumed.
        binary_path = os.path.join(bundle_path, "Contents", "MacOS", APP_NAME)
        return binary_path if os.path.isfile(binary_path) else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _launch_in_background():
    # Second design here, not the first — the first launched the resolved binary directly via
    # subprocess.Popen, bypassing `open`/Launch Services entirely, specifically to get
    # unambiguous argv delivery after `open -a SonoScript --args --browser-launch` turned out
    # NOT to reliably reach sys.argv in the packaged app (confirmed via real user testing: the
    # window still stole focus). That traded one real bug for another, also confirmed via real
    # testing: launched that way, the app crashed on a Gatekeeper block on a temp .dylib
    # (llvmlite, extracted fresh on every launch) that a normal launch doesn't hit. The likely
    # reason: `open`/Launch Services gives a launched app a clean, standard user-session
    # environment: this file inherits whatever Chrome hands ITS OWN native-messaging host
    # process, and a subprocess launched directly from that inherits the same — plausibly a
    # different TMPDIR (or similar) that a Gatekeeper-relevant extraction lands somewhere it
    # otherwise wouldn't.
    #
    # Fixed by going back to `open` for the environment, but replacing the unreliable --args
    # flag with a marker file instead: this host writes PENDING_BROWSER_LAUNCH_PATH before
    # launching, and main.py deletes it (not just checks it — see the comment there) at startup
    # to learn this launch came from the browser extension. A file this host directly controls
    # the writing of has none of argv's forwarding uncertainty through Launch Services and a
    # frozen py2app bundle.
    try:
        os.makedirs(os.path.dirname(PENDING_BROWSER_LAUNCH_PATH), exist_ok=True)
        with open(PENDING_BROWSER_LAUNCH_PATH, "w"):
            pass
    except OSError:
        pass  # worst case main.py shows its window on this launch — not silently broken either way

    bundle_path = None
    binary_path = _find_app_binary()
    if binary_path:
        # Contents/MacOS/SonoScript -> the .app bundle two directories up.
        bundle_path = os.path.dirname(os.path.dirname(os.path.dirname(binary_path)))
    try:
        if bundle_path:
            subprocess.Popen(
                ["open", "-g", bundle_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            # Best-effort fallback if Launch Services couldn't resolve the app by name for some
            # reason — the exact same `open -a NAME` path, just without a resolved bundle path
            # to hand it directly.
            subprocess.Popen(
                ["open", "-g", "-a", APP_NAME],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except OSError:
        pass  # background.js's own retry loop will simply keep seeing "not up yet"


def _get_token():
    try:
        with open(TOKEN_PATH) as f:
            return f.read().strip()
    except OSError:
        return None


def main():
    message = read_message()
    if message is None:
        return

    if _bridge_is_up():
        token = _get_token()
        if token is None:
            # The port is up but the token file read failed — a genuinely unexpected state
            # (browser_bridge.start() writes it before the port ever becomes connectable), not
            # something a retry is likely to fix on its own.
            send_message({"error": "SonoScript doesn't appear to be running."})
            return
        send_message({"token": token})
        return

    if not _launch_recently_triggered():
        _mark_launch_triggered()
        _launch_in_background()
    send_message({"launching": True})


if __name__ == "__main__":
    main()
