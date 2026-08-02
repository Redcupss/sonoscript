import json
import os
import time

from history import trash_file

# A real, user-visible folder — distinct from history.py's internal cache, which is
# deliberately not meant to be browsed. This one is: the whole point is that recovering a
# trashed file back into it, or dragging a new one in from Finder, should just work, which is
# why this module has no separate index file the way history.py does — it scans the actual
# folder contents live every time, so it can never drift out of sync with what's really there.
DEFAULT_SAVE_DIR = os.path.expanduser("~/Documents/SonoScript")


def ensure_dir(save_dir):
    os.makedirs(save_dir, exist_ok=True)


def _sidecar_path(wav_path):
    # Dot-prefixed so Finder hides it by default (its own standard convention, same as
    # .DS_Store) — this folder is meant to look like a clean list of recordings, not a wav
    # sitting next to a same-named .json a user has no reason to open. Every other place that
    # touches a sidecar (save_generation, list_saved, delete_saved) goes through this same
    # function, so the naming only needs to change here.
    directory, filename = os.path.split(wav_path)
    base = os.path.splitext(filename)[0]
    return os.path.join(directory, f".{base}.json")


def _safe_filename(text):
    # A real Finder folder benefits from a human-recognizable name far more than history.py's
    # internal cache does (which is why that one just uses a random id) — derived from the
    # generation's own text, since that's the only thing a user would actually recognize it by.
    base = "".join(c if c.isalnum() or c in " -_" else "" for c in text[:40]).strip()
    return base or "Recording"


def save_generation(save_dir, text, provider, voice, wav_bytes):
    """Writes a generation into the permanent save folder as a real .wav plus a small JSON
    sidecar (text/provider/voice) carrying the metadata a plain .wav can't. Returns the path
    written. Filename collisions get a numeric suffix rather than overwriting."""
    ensure_dir(save_dir)
    base = _safe_filename(text)
    filename = base + ".wav"
    path = os.path.join(save_dir, filename)
    n = 1
    while os.path.exists(path):
        n += 1
        filename = f"{base} {n}.wav"
        path = os.path.join(save_dir, filename)
    with open(path, "wb") as f:
        f.write(wav_bytes)
    sidecar = {"text": text, "provider": provider, "voice": voice, "saved_at": time.time()}
    with open(_sidecar_path(path), "w") as f:
        json.dump(sidecar, f, indent=2)
    return path


def list_saved(save_dir):
    """Scans save_dir directly (not an index) — a file recovered from the Trash back into
    this folder, or dragged in fresh from Finder, shows up automatically. Files with no
    sidecar (anything not saved from within the app) still show up, just without text/
    provider/voice metadata."""
    if not os.path.isdir(save_dir):
        return []
    entries = []
    for name in os.listdir(save_dir):
        if not name.lower().endswith(".wav"):
            continue
        path = os.path.join(save_dir, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        meta = {}
        sidecar = _sidecar_path(path)
        if os.path.exists(sidecar):
            try:
                with open(sidecar) as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                meta = {}
        entries.append({
            "filename": name,
            "path": path,
            "text": meta.get("text", ""),
            "provider": meta.get("provider", ""),
            "voice": meta.get("voice", ""),
            "size_bytes": stat.st_size,
            "modified_at": stat.st_mtime,
        })
    entries.sort(key=lambda e: e["modified_at"], reverse=True)
    return entries


def delete_saved(path):
    """Moves both the .wav and its sidecar (if any) to the Trash — recoverable, same as
    history.py's own deletion, via the same trash_file helper."""
    trash_file(path)
    sidecar = _sidecar_path(path)
    if os.path.exists(sidecar):
        trash_file(sidecar)
