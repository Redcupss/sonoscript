#!/usr/bin/env python3
"""Mint a SonoScript Sesame license key. Dev-only — never imported by main.py, so py2app's
modulegraph (which only traces from main.py) structurally can't pull this or the private
signing key into the shipped app.

Usage:
    tools/sign_license.py NAME-OR-ID [--expires 2026-12-31]

Prints a SONO1.<payload>.<sig> string to hand to the customer. Verify it against license.py's
SESAME_PUBLIC_KEY_HEX (regenerate that constant with --show-public-key if the signing key here
is ever rotated).

The private key lives at ~/.sonoscript/signing_key.bin — NEVER commit it, and never let it sit
in an iCloud-synced folder, a git repo, or any other synced location. Anyone who gets a copy of
that file can mint valid licenses forever.
"""
import argparse
import base64
import json
import os
import sys
from datetime import datetime, timezone

from nacl.signing import SigningKey

KEY_PATH = os.path.expanduser("~/.sonoscript/signing_key.bin")
LICENSE_TAG = "SONO1"


def _load_or_create_key():
    os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as f:
            return SigningKey(f.read())
    sk = SigningKey.generate()
    with open(KEY_PATH, "wb") as f:
        f.write(bytes(sk))
    os.chmod(KEY_PATH, 0o600)
    print(f"Generated a new signing key at {KEY_PATH} — back this up somewhere safe and "
          f"private (NOT this repo, NOT iCloud). Losing it means you can never mint another "
          f"valid license for existing installs.", file=sys.stderr)
    return sk


def _b64e(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign_license(license_id, expires=None):
    sk = _load_or_create_key()
    payload = {
        "product": "sesame",
        "id": license_id,
        "iat": datetime.now(timezone.utc).isoformat(),
        "exp": expires,
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signed = sk.sign(payload_bytes)
    return f"{LICENSE_TAG}.{_b64e(payload_bytes)}.{_b64e(signed.signature)}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("license_id", nargs="?", help="a short id/name for this license, e.g. a customer name")
    parser.add_argument("--expires", help="ISO date the license stops working, e.g. 2026-12-31 (omit for lifetime)")
    parser.add_argument("--show-public-key", action="store_true", help="print the public key hex and exit")
    args = parser.parse_args()

    if args.show_public_key:
        sk = _load_or_create_key()
        print(sk.verify_key.encode().hex())
        sys.exit(0)

    if not args.license_id:
        parser.error("license_id is required unless --show-public-key is given")

    expires = None
    if args.expires:
        expires = datetime.fromisoformat(args.expires).replace(tzinfo=timezone.utc).isoformat()

    print(sign_license(args.license_id, expires))
