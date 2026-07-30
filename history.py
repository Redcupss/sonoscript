import json
import os
import time
import uuid

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

# 24kHz/16-bit mono PCM — matches every local provider's actual output in this app (see
# _requestChatterboxTTS/_requestSesameTTS). Good enough for a UI estimate ("this cache size
# holds about N minutes"), not meant to be exact for provider/format combinations this app
# doesn't actually produce.
BYTES_PER_SECOND_ESTIMATE = 24000 * 2


def estimate_seconds_for_bytes(num_bytes):
    return num_bytes / BYTES_PER_SECOND_ESTIMATE


def _ensure_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


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


def add_entry(text, provider, voice, speed, wav_bytes):
    """Writes a completed generation into the cache, evicting least-recently-used entries
    first if the configured max count/size is now exceeded. Returns the new entry dict."""
    _ensure_dir()
    data = load_index()
    entry_id = uuid.uuid4().hex[:12]
    filename = f"{entry_id}.wav"
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
        try:
            os.remove(os.path.join(CACHE_DIR, victim["audio_file"]))
        except OSError:
            pass


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
            try:
                os.remove(os.path.join(CACHE_DIR, e["audio_file"]))
            except OSError:
                pass
        else:
            remaining.append(e)
    data["entries"] = remaining
    save_index(data)


def set_limits(max_entries=None, max_bytes=None):
    data = load_index()
    if max_entries is not None:
        data["max_entries"] = max_entries
    if max_bytes is not None:
        data["max_bytes"] = max_bytes
    _evict(data)
    save_index(data)
    return data
