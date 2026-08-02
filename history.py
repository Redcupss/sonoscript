import json
import os
import time
import uuid

import Foundation

from config import CONFIG_PATH

# App-internal, not meant to be browsed in Finder — distinct from the user-visible permanent
# save folder (default ~/Documents/SonoScript, chosen by the user), which is a separate,
# not-yet-built piece of this feature. This is just the automatic "recent generations" cache:
# entries appear here only once a generation has actually finished playing start to finish, so
# an interrupted/abandoned take (stopped mid-way to tweak voice/speed/text) never shows up.
CACHE_DIR = os.path.join(os.path.dirname(CONFIG_PATH), "cache")
CACHE_INDEX_PATH = os.path.join(CACHE_DIR, "cache_index.json")

DEFAULT_MAX_ENTRIES = 10
DEFAULT_MAX_BYTES = None  # None = no size cap, only the count cap, until the user sets one

# Marks "leave this field alone" in set_limits/preview_eviction — None is itself a legitimate
# value for max_bytes (it's exactly how "unlimited" is represented), so it has to be a real
# settable value, not overloaded to also mean "don't change it."
_UNSET = object()

# 24kHz/16-bit mono PCM — matches every local provider's actual output in this app (see
# _requestChatterboxTTS/_requestSesameTTS). Good enough for a UI estimate ("this cache size
# holds about N minutes"), not meant to be exact for provider/format combinations this app
# doesn't actually produce.
BYTES_PER_SECOND_ESTIMATE = 24000 * 2


def estimate_seconds_for_bytes(num_bytes):
    return num_bytes / BYTES_PER_SECOND_ESTIMATE


def _ensure_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def trash_file(path):
    # Moves to the real macOS Trash instead of unlinking outright — a wrong limit change or
    # an accidental delete is recoverable (drag back out of Trash, or it just ages out
    # naturally) instead of being instantly, permanently gone. NSFileManager (not NSWorkspace)
    # deliberately — it's documented safe to call off the main thread, and eviction can happen
    # from add_entry, which doesn't run on the main thread.
    try:
        fm = Foundation.NSFileManager.defaultManager()
        url = Foundation.NSURL.fileURLWithPath_(path)
        success, _, error = fm.trashItemAtURL_resultingItemURL_error_(url, None, None)
        if not success:
            os.remove(path)
    except OSError:
        pass


def load_index():
    try:
        with open(CACHE_INDEX_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data.setdefault("max_entries", DEFAULT_MAX_ENTRIES)
    data.setdefault("max_bytes", DEFAULT_MAX_BYTES)
    data.setdefault("entries", [])
    return data


def save_index(data):
    _ensure_dir()
    tmp = CACHE_INDEX_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, CACHE_INDEX_PATH)


def _safe_filename(text):
    # Same sanitizing rule saved.py's permanent-folder filenames use — kept as its own small
    # copy here rather than importing from saved.py, which already imports FROM this module
    # (trash_file); importing back the other way would be circular.
    base = "".join(c if c.isalnum() or c in " -_" else "" for c in text[:40]).strip()
    return base or "Recording"


def add_entry(text, provider, voice, speed, wav_bytes):
    """Writes a completed generation into the cache, evicting least-recently-used entries
    first if the configured max count/size is now exceeded. Returns the new entry dict."""
    _ensure_dir()
    data = load_index()
    entry_id = uuid.uuid4().hex[:12]
    # entry_id alone (no readable text) used to be the whole filename — fine as an internal
    # lookup key, but if anyone actually opens this folder (Finder, a debug log, this session's
    # own screenshots) a bare hex string tells them nothing. The id still stays the real,
    # stable lookup key everywhere (entry["id"]/entry["audio_file"] is what every caller
    # actually uses to find the file) — this only changes what the file is NAMED.
    filename = f"{_safe_filename(text)} - {entry_id}.wav"
    with open(os.path.join(CACHE_DIR, filename), "wb") as f:
        f.write(wav_bytes)
    now = time.time()
    entry = {
        "id": entry_id,
        "text": text,
        "provider": provider,
        "voice": voice,
        "speed": speed,
        "audio_file": filename,
        "size_bytes": len(wav_bytes),
        "created_at": now,
        "last_accessed_at": now,
    }
    data["entries"].append(entry)
    _evict(data)
    save_index(data)
    return entry


def _evict(data):
    max_entries = data.get("max_entries") or DEFAULT_MAX_ENTRIES
    max_bytes = data.get("max_bytes")
    entries = data["entries"]
    entries.sort(key=lambda e: e.get("last_accessed_at", 0))  # oldest-used first
    while len(entries) > max_entries or (max_bytes and sum(e["size_bytes"] for e in entries) > max_bytes):
        victim = entries.pop(0)
        trash_file(os.path.join(CACHE_DIR, victim["audio_file"]))


def preview_eviction(max_entries=_UNSET, max_bytes=_UNSET):
    """How many currently-cached entries WOULD be evicted if these limits were applied right
    now, without changing anything — lets a caller warn the user before set_limits actually
    deletes anything. Unset params fall back to whatever's currently stored, matching
    set_limits' own "leave this field alone" semantics, so a caller only changing one of the
    two limits gets an accurate preview against the other one's current value."""
    data = load_index()
    resolved_max_entries = data.get("max_entries") if max_entries is _UNSET else max_entries
    resolved_max_bytes = data.get("max_bytes") if max_bytes is _UNSET else max_bytes
    resolved_max_entries = resolved_max_entries or DEFAULT_MAX_ENTRIES
    entries = sorted(data["entries"], key=lambda e: e.get("last_accessed_at", 0))
    count = len(entries)
    total = sum(e["size_bytes"] for e in entries)
    evicted = 0
    i = 0
    while count > resolved_max_entries or (resolved_max_bytes and total > resolved_max_bytes):
        total -= entries[i]["size_bytes"]
        count -= 1
        evicted += 1
        i += 1
    return evicted


def list_entries():
    data = load_index()
    return sorted(data["entries"], key=lambda e: e.get("created_at", 0), reverse=True)


def touch_entry(entry_id):
    """Marks an entry as just used (e.g. replayed from the cache), protecting it from LRU
    eviction a while longer."""
    data = load_index()
    for e in data["entries"]:
        if e["id"] == entry_id:
            e["last_accessed_at"] = time.time()
            break
    save_index(data)


def remove_entry(entry_id):
    data = load_index()
    remaining = []
    for e in data["entries"]:
        if e["id"] == entry_id:
            trash_file(os.path.join(CACHE_DIR, e["audio_file"]))
        else:
            remaining.append(e)
    data["entries"] = remaining
    save_index(data)


def set_limits(max_entries=_UNSET, max_bytes=_UNSET):
    data = load_index()
    if max_entries is not _UNSET:
        data["max_entries"] = max_entries
    if max_bytes is not _UNSET:
        data["max_bytes"] = max_bytes
    _evict(data)
    save_index(data)
    return data
