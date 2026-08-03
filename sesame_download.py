"""Downloads and extracts the Sesame stock voices + model (~1.6GB) on demand, the first time a
valid Sesame license key is entered. Too large to bundle in the shipped app itself (it would
roughly double the download for every user, including the ~90%+ who'll only ever use System/
Chatterbox) — fetched instead from a GitHub release asset, the same distribution mechanism
already used for the app itself, so this needs no separate hosting.

Downloads to a temp location first and only swaps it into place on full, hash-verified success —
a dropped connection mid-download must never leave a half-extracted, silently-broken sesame_assets
dir behind for the next launch to trip over.
"""
import hashlib
import os
import shutil
import ssl
import urllib.error
import urllib.request
import zipfile

import certifi

from config import SESAME_ASSETS_DIR

# Same pattern as main.py's own SSL_CONTEXT — the embedded/framework Python on macOS doesn't
# reliably fall back to the system certificate store, which makes a plain urlopen() fail with
# CERTIFICATE_VERIFY_FAILED even against a legitimate host like github.com. Confirmed directly:
# this exact failure happened on first real download attempt without this.
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

ASSETS_URL = "https://github.com/Redcupss/sonoscript/releases/download/v1.11.0/sesame-assets-v1.11.0.zip"
ASSETS_SHA256 = "0d94dc7cd1b45be4cdb513b3ff68f5a9be9a3c159d79c91b01cdb7731e09cebc"
ASSETS_SIZE_BYTES = 2000512259  # informational fallback; the real total comes from the response

# The 5 stock voices' reference clips (see SESAME_VOICES in main.py) — checking all 5, not just
# one, so a partial/interrupted extraction from a previous attempt reads as "not ready" rather
# than silently offering voices whose audio file is actually missing.
_EXPECTED_VOICE_FILES = ["sadie.wav", "manny.wav", "ben.wav", "conversational_a.wav", "conversational_b.wav"]
_MODEL_SNAPSHOTS_DIR = os.path.join(
    SESAME_ASSETS_DIR, "hf_cache", "hub", "models--mlx-community--csm-1b-8bit", "snapshots")
# Sesame's model code loads a LLaMA3 tokenizer separately, by HuggingFace repo id, independent
# of the CSM model snapshot above — see the long comment in main.py's _sesameEngine. A download
# from before this was added would satisfy every other check here while still missing this,
# so it needs its own explicit check for sesame_assets_ready() to mean what it says.
_TOKENIZER_SNAPSHOTS_DIR = os.path.join(
    SESAME_ASSETS_DIR, "hf_cache", "hub", "models--unsloth--Llama-3.2-1B", "snapshots")
# Same story a third time — Sesame's audio codec ("Mimi") is ALSO fetched separately, by its
# own HuggingFace repo id, independent of both the CSM weights and the tokenizer above.
_MIMI_SNAPSHOTS_DIR = os.path.join(
    SESAME_ASSETS_DIR, "hf_cache", "hub", "models--kyutai--moshiko-pytorch-bf16", "snapshots")


class DownloadError(Exception):
    pass


def sesame_assets_ready():
    """True if every stock voice clip, the model snapshot, tokenizer, and codec are present."""
    voices_dir = os.path.join(SESAME_ASSETS_DIR, "voices")
    if not all(os.path.isfile(os.path.join(voices_dir, f)) for f in _EXPECTED_VOICE_FILES):
        return False
    for snapshots_dir in (_MODEL_SNAPSHOTS_DIR, _TOKENIZER_SNAPSHOTS_DIR, _MIMI_SNAPSHOTS_DIR):
        if not (os.path.isdir(snapshots_dir) and bool(os.listdir(snapshots_dir))):
            return False
    return True


def download_sesame_assets(progress_cb=None):
    """Downloads, hash-verifies, and extracts the Sesame assets zip into SESAME_ASSETS_DIR.

    progress_cb(downloaded_bytes, total_bytes) is called from THIS thread as chunks arrive —
    callers must marshal any UI updates to the main thread themselves, same as every other
    background-thread callback in this app. Raises DownloadError with a message safe to show
    the user directly on any failure (network, hash mismatch, corrupt zip)."""
    parent = os.path.dirname(SESAME_ASSETS_DIR)
    os.makedirs(parent, exist_ok=True)
    tmp_zip = os.path.join(parent, "sesame_assets_download.tmp")
    tmp_extract = os.path.join(parent, "sesame_assets_extract.tmp")
    try:
        req = urllib.request.Request(ASSETS_URL, headers={"User-Agent": "SonoScript"})
        hasher = hashlib.sha256()
        downloaded = 0
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
            total = int(resp.headers.get("Content-Length") or ASSETS_SIZE_BYTES)
            with open(tmp_zip, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(downloaded, total)

        if hasher.hexdigest() != ASSETS_SHA256:
            raise DownloadError("The download didn't match what was expected. Check your connection and try again.")

        if os.path.isdir(tmp_extract):
            shutil.rmtree(tmp_extract)
        with zipfile.ZipFile(tmp_zip) as zf:
            zf.extractall(tmp_extract)

        if os.path.isdir(SESAME_ASSETS_DIR):
            shutil.rmtree(SESAME_ASSETS_DIR)
        os.rename(tmp_extract, SESAME_ASSETS_DIR)

        if not sesame_assets_ready():
            raise DownloadError("The download finished but some files were missing. Try again.")
    except DownloadError:
        raise
    except (OSError, urllib.error.URLError, zipfile.BadZipFile) as e:
        raise DownloadError(f"Couldn't download Sesame voices: {e}")
    finally:
        if os.path.exists(tmp_zip):
            os.remove(tmp_zip)
        if os.path.isdir(tmp_extract):
            shutil.rmtree(tmp_extract, ignore_errors=True)
