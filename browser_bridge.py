"""Local-only HTTP listener the SonoScript browser extension hands selected text off to.

Binds to 127.0.0.1 only — never reachable from the network, only from processes on this same
Mac. Every request must carry a per-launch random token (see TOKEN_PATH) that a website's own
JavaScript has no way to obtain: web pages have no filesystem access at all (a browser-level
restriction, unrelated to CORS), so a file only this Mac's current user can read is invisible to
them regardless of network access. The only party meant to ever read that file and relay it to
the extension is the native-messaging host (native_host.py) — Chrome/Edge structurally prevents
a web page from reaching native messaging at all, only the extension's own code can, and only
after Chrome verifies the extension's ID against the host's own allowlist. That's the real trust
chain: file → native messaging host → extension → this server, with no step a malicious page can
join.
"""
import base64
import hashlib
import hmac
import http.server
import json
import os
import secrets
import socketserver
import struct
import threading
import urllib.parse

PORT = 51823
TOKEN_PATH = os.path.expanduser("~/Library/Application Support/SonoScript/bridge_token")
TOKEN_HEADER = "X-SonoScript-Token"
# The extension's own ID, derived from the "key" pinned in manifest.json — matches
# com.sonoscript.bridge.json's allowed_origins, which is the same pin applied on the native-
# messaging side of this trust chain.
EXTENSION_ORIGIN = "chrome-extension://jolmpfmkkfngeolncggdghhmndjbjcng"
# Generous for any real pasted/extracted article — a normal long-form article runs well under
# 100KB of plain text — while still giving a slow/malicious sender a hard ceiling rather than
# letting the server read an unbounded body into memory.
MAX_BODY_BYTES = 2_000_000

# The in-page toolbar's live control channel (play/pause/seek/voice, state/word-timing pushed
# back) — see _WebSocketConnection below. RFC 6455's fixed handshake-response magic string.
CONTROL_PATH = "/control"
_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _ws_accept_key(client_key):
    digest = hashlib.sha1((client_key + _WS_MAGIC).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _new_token():
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    token = secrets.token_hex(32)
    # os.open with the mode set at creation (not a separate chmod() after a plain open()) means
    # there's no window where the file briefly exists world-readable before permissions lock
    # down — the two used to be separate steps, and a reader racing exactly that gap could have
    # read the token off disk during it.
    fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    return token


def _recv_exact(sock, n):
    """Reads exactly n bytes, or returns None if the connection closes first."""
    if n == 0:
        return b""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


class _WebSocketConnection:
    """A single toolbar's live control channel, hand-rolled against RFC 6455 rather than a
    third-party dependency — matches this file's own existing minimal-stdlib-only design
    (see native_host.py's docstring for the same reasoning), and this channel only ever needs
    to carry small JSON control messages, not the full range of what the spec allows. Only
    handles a single, unfragmented text frame per message (opcode 0x1, FIN=1) — the only shape
    a real browser's `ws.send(JSON.stringify(...))` produces for a message this small; anything
    else (binary, fragmented) is treated as a protocol violation and drops the connection
    cleanly rather than attempting to reassemble something that should never actually happen
    here. Client→server frames must be masked per spec; server→client frames must NOT be."""

    def __init__(self, sock):
        self.sock = sock
        self._send_lock = threading.Lock()

    def recv_json(self):
        while True:
            header = _recv_exact(self.sock, 2)
            if header is None:
                return None
            b0, b1 = header[0], header[1]
            fin = b0 & 0x80
            opcode = b0 & 0x0F
            masked = b1 & 0x80
            length = b1 & 0x7F
            if length == 126:
                ext = _recv_exact(self.sock, 2)
                if ext is None:
                    return None
                length = struct.unpack("!H", ext)[0]
            elif length == 127:
                ext = _recv_exact(self.sock, 8)
                if ext is None:
                    return None
                length = struct.unpack("!Q", ext)[0]
            mask_key = _recv_exact(self.sock, 4) if masked else None
            payload = _recv_exact(self.sock, length)
            if payload is None:
                return None
            if masked and mask_key:
                payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:  # close
                self._send_frame(0x8, b"")
                return None
            if opcode == 0x9:  # ping
                self._send_frame(0xA, payload)  # pong
                continue
            if opcode == 0xA:  # pong — nothing to do, this side never pings
                continue
            if opcode != 0x1 or not fin:
                return None
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                continue  # ignore one malformed message, keep listening for the next

    def _send_frame(self, opcode, payload):
        header = bytes([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header += bytes([length])
        elif length < 65536:
            header += bytes([126]) + struct.pack("!H", length)
        else:
            header += bytes([127]) + struct.pack("!Q", length)
        with self._send_lock:
            self.sock.sendall(header + payload)

    def send_json(self, obj):
        try:
            self._send_frame(0x1, json.dumps(obj).encode("utf-8"))
        except OSError:
            pass  # client already gone — broadcasts are best-effort, not guaranteed delivery

    def close(self):
        try:
            self._send_frame(0x8, b"")
            self.sock.close()
        except OSError:
            pass


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A set, not a single slot — SonoScript only ever has ONE real playback session (one
        # player, one all_chunks) at a time, same as the app's own single window, so every
        # connected toolbar is legitimately watching/controlling that same one session. A single
        # slot that closed whichever connection was there before a new one registered was wrong
        # in two ways at once: opening a second tab's toolbar (or, during testing, just a second
        # browser) kicked the first one's connection, and closing triggered THAT tab's own
        # reconnect-with-backoff loop, which kicked the second one right back — the two
        # connections fought over the one slot indefinitely, showing up as the toolbar's opacity
        # visibly flipping between connected/disconnected every couple seconds in each tab,
        # opposite to the other. Multiple simultaneously-open toolbars all showing the same live
        # state together (instead of racing to be the only one connected) is the correct
        # behavior here, not a bug to prevent.
        self._control_conns = set()
        self._control_lock = threading.Lock()

    def add_control_connection(self, conn):
        with self._control_lock:
            self._control_conns.add(conn)

    def remove_control_connection(self, conn):
        # The stop must fire from INSIDE the same lock the empty-check runs under, not after
        # releasing it — confirmed via review this was a real race otherwise: a concurrent
        # add_control_connection() from a second tab's toolbar (e.g. duplicating a tab, or one
        # tab closing just as another with the same article opens) could slip in between a
        # released lock and this call, leaving a live connection that still gets spuriously
        # stopped by a now-stale "empty" snapshot. Safe to call on_control() while holding the
        # lock: it dispatches into main.py via performSelectorOnMainThread_withObject_waitUntilDone_
        # with waitUntilDone=False, which is non-blocking and returns immediately — no deadlock.
        with self._control_lock:
            self._control_conns.discard(conn)
            now_empty = len(self._control_conns) == 0
            if now_empty:
                # The toolbar is the only control surface a browser-triggered read has. If its
                # connection just dropped — closed tab, navigated away, page reload — and no OTHER
                # toolbar is still watching this same session (see the class docstring above for
                # why multiple simultaneously-open toolbars are a real, intended case), there's no
                # way left to pause or stop playback at all: it would just keep reading
                # indefinitely with nothing able to reach it. Confirmed directly this was a real
                # problem, not a hypothetical one — reported after navigating away mid-playback
                # left it running with no control surface anywhere. toolbar.js's own close (×)
                # button already sends this same {cmd: "stop"} explicitly before it closes its
                # connection; this covers every OTHER way the connection can end, where that
                # explicit send never gets the chance to run because the page itself is already
                # gone.
                self.on_control({"cmd": "stop"})

    def broadcast_state(self, payload):
        """Best-effort push to every currently-connected control client — a silent no-op if
        none are connected. Safe from any thread."""
        with self._control_lock:
            conns = list(self._control_conns)
        for conn in conns:
            conn.send_json(payload)


class _ReadRequestHandler(http.server.BaseHTTPRequestHandler):
    # Bounds how long a single request can sit open (a slow/partial send, deliberate or not) —
    # BaseHTTPRequestHandler is a socketserver.StreamRequestHandler under the hood, which reads
    # this class attribute and applies it as the connection's socket timeout. Paired with
    # _ThreadingHTTPServer above so one slow request can no longer wedge every future request
    # behind it (the plain single-threaded HTTPServer this used to be could only ever serve one
    # request at a time, with no timeout at all).
    timeout = 10

    def log_message(self, format, *args):
        # The default per-request access log would just be noise on top of this app's own
        # console output — silence it rather than fighting it.
        pass

    def _send_cors_headers(self):
        # A browser extension's fetch() call comes from a chrome-extension:// origin, not
        # http(s)://, so this always needs an explicit allow rather than same-origin working
        # for free. Restricted to the real extension's own origin (not "*") — the token check
        # below is still the real access-control gate, but a wildcard here let ANY website's
        # JavaScript read this server's responses too, which is a narrow but real "is SonoScript
        # installed and running" fingerprinting signal a page had no legitimate reason to get.
        origin = self.headers.get("Origin", "")
        if origin == EXTENSION_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", f"Content-Type, {TOKEN_HEADER}")

    def _reject(self, code):
        self.send_response(code)
        self._send_cors_headers()
        self.end_headers()

    def _authorized(self):
        # Constant-time comparison — this token never needs to be fast to check, and a
        # timing-based comparison would leak how many leading characters a guess got right.
        # compare_digest itself can raise TypeError on a header value that isn't plain ASCII —
        # an unauthorized/malformed request, same as any other failed check here, not a crash.
        try:
            return hmac.compare_digest(
                self.headers.get(TOKEN_HEADER, ""), self.server.token)
        except TypeError:
            return False

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != CONTROL_PATH:
            self._reject(404)
            return
        # A page's own WebSocket() constructor has no way to set a custom header at all (a real,
        # long-standing web-platform limitation — unlike fetch(), there's no headers option), so
        # the same per-launch token used everywhere else has to travel as a query parameter here
        # instead of the X-SonoScript-Token header the POST /read path uses. Still loopback-only
        # and still opaque/per-launch; also never a real page navigation (only ever opened
        # programmatically from the extension's own background script), so unlike a typed/
        # clicked URL it never lands in browser history.
        token = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
        try:
            authorized = hmac.compare_digest(token, self.server.token)
        except TypeError:
            authorized = False
        # No Origin check here, unlike the POST /read path — and deliberately so, not an
        # oversight. toolbar.js runs as a content script INJECTED INTO THE PAGE (via
        # chrome.scripting.executeScript), so a WebSocket it opens carries the Origin of
        # whatever page it's running on (e.g. https://www.nytimes.com) — never
        # chrome-extension://..., which only applies to requests made from the extension's own
        # background service worker context (that's why POST /read's Origin check, made from
        # background.js's own fetch() call, works correctly). Checking Origin here would reject
        # every real connection from every real page — confirmed directly: it did. The token is
        # already the real, sufficient gate (same refrain as the POST /read path) — it can only
        # ever reach a page via native messaging, a channel no page script can touch regardless
        # of origin, so Origin-based defense-in-depth adds nothing real for this specific
        # channel and was actively breaking the feature.
        if not authorized:
            self._reject(403)
            return
        client_key = self.headers.get("Sec-WebSocket-Key")
        if self.headers.get("Upgrade", "").lower() != "websocket" or not client_key:
            self._reject(400)
            return
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", _ws_accept_key(client_key))
        self.end_headers()
        # This connection is now a long-lived control channel, not a one-shot request — the
        # class-level 10s timeout exists to stop a STALLED one-shot request from wedging a
        # thread forever, which would wrongly kill a healthy, simply-idle toolbar connection.
        self.connection.settimeout(None)
        conn = _WebSocketConnection(self.connection)
        self.server.add_control_connection(conn)
        try:
            while True:
                cmd = conn.recv_json()
                if cmd is None:
                    break
                if isinstance(cmd, dict):
                    self.server.on_control(cmd)
        except OSError:
            # A page navigating away or closing the tab drops this connection at the TCP level
            # rather than sending a clean WebSocket close frame — recv_json's own _recv_exact
            # surfaces that as ConnectionResetError (a subclass of OSError). Same as any other
            # client just going away: nothing to do but let this handler's thread end quietly.
            pass
        finally:
            self.server.remove_control_connection(conn)

    def do_OPTIONS(self):
        # A JSON POST body (and the custom token header) triggers a CORS preflight OPTIONS
        # request first — without answering this, the browser blocks the real POST before
        # it's ever sent. No token check here: preflight never carries custom headers itself.
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path != "/read":
            self._reject(404)
            return
        if not self._authorized():
            self._reject(403)
            return
        try:
            # int() on a malformed/missing Content-Length, and payload["text"] on a JSON body
            # that parsed fine but wasn't a dict (a bare list or string) both used to raise
            # outside this try block entirely — genuinely malformed input crashing the request
            # instead of getting the same clean 400 a truncated/invalid body already gets.
            length = int(self.headers.get("Content-Length", 0))
            # rfile.read() treats ANY negative size as "read until the connection closes" —
            # a negative Content-Length would otherwise sail past the size check below (it's
            # not > MAX_BODY_BYTES) and then block the whole request for the full socket
            # timeout instead of getting the same immediate 400 every other malformed header
            # shape gets here.
            if length < 0:
                raise ValueError
            if length > MAX_BODY_BYTES:
                self._reject(413)
                return
            payload = json.loads(self.rfile.read(length))
            text = payload["text"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            self._reject(400)
            return
        self.server.on_text(text)
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')


def start(on_text, on_control):
    """Starts the listener on its own daemon thread and returns immediately.

    Generates a fresh token for this launch (native_host.py reads it back off disk to hand to
    the extension — see this module's own docstring for the full trust chain). on_text(text)
    fires once per valid, authorized POST /read request; on_control(cmd_dict) fires once per
    valid JSON message received on an open GET /control WebSocket connection. Both are called
    from THIS listener's background threads — never the main/UI thread. The caller must hop to
    the main thread (e.g. via performSelectorOnMainThread_withObject_waitUntilDone_) before
    touching any AppKit object, same rule as every other background callback in this app.

    The returned server also exposes broadcast_state(payload) for pushing live state (playback
    position, current word, voice list) out to whatever control connection is currently open —
    safe to call from any thread, and a silent no-op if no toolbar is connected.
    """
    server = _ThreadingHTTPServer(("127.0.0.1", PORT), _ReadRequestHandler)
    server.token = _new_token()
    server.on_text = on_text
    server.on_control = on_control
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
