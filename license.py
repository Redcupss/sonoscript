import base64
import json
from datetime import datetime, timezone

from nacl.signing import VerifyKey

# Safe to ship — this is the PUBLIC half of the keypair, verify-only. The private half that
# signs new licenses lives only in the developer's own ~/.sonoscript/signing_key.bin, generated
# and used by tools/sign_license.py, which main.py never imports (so py2app's modulegraph,
# which only traces from main.py, structurally can't pull it into the shipped bundle).
SESAME_PUBLIC_KEY_HEX = "71de6be9d8683ffe089412198ae8c118f6f72c1e1438d979cd533c1679f5a748"
LICENSE_TAG = "SONO1"  # a literal version tag lets a future SONO2 format coexist with old keys
REVOKED_LICENSE_IDS = frozenset()  # add a leaked/refunded license's "id" here, ship in an update


class LicenseError(Exception):
    pass


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_license(license_str):
    """Returns the parsed payload dict for a valid, unexpired, unrevoked license, or raises
    LicenseError. Pure/local — no network call, matching every other provider in this app.

    Every failure mode (malformed base64, bad JSON, a bad signature, a malformed exp string)
    collapses to one clean LicenseError rather than an uncaught exception — this is called
    from _isConfigured(), which gates showMainScreen(), the welcome screen, and the Play
    button, so it must never crash its caller."""
    try:
        parts = (license_str or "").strip().split(".")
        if len(parts) != 3:
            raise ValueError("malformed license")
        tag, payload_b64, sig_b64 = parts
        if tag != LICENSE_TAG:
            raise LicenseError("Unrecognized license format.")
        payload_bytes = _b64d(payload_b64)
        sig_bytes = _b64d(sig_b64)
        VerifyKey(bytes.fromhex(SESAME_PUBLIC_KEY_HEX)).verify(payload_bytes, sig_bytes)
        payload = json.loads(payload_bytes)
        if payload.get("product") != "sesame":
            raise LicenseError("This key isn't for Sesame voices.")
        if payload.get("id") in REVOKED_LICENSE_IDS:
            raise LicenseError("This license has been revoked.")
        exp = payload.get("exp")
        if exp:
            exp_dt = datetime.fromisoformat(exp)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp_dt:
                raise LicenseError("This license has expired.")
        return payload
    except LicenseError:
        raise
    except Exception:
        raise LicenseError("This license key isn't valid.")
