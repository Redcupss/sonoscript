import json
import os

CONFIG_PATH = os.path.expanduser("~/Library/Application Support/SonoScript/config.json")
SESAME_VOICES_DIR = os.path.join(os.path.dirname(CONFIG_PATH), "sesame_voices")
# Distinct from SESAME_VOICES_DIR above: that one holds each user's OWN recorded voice clones
# (custom_*.wav). This one holds the stock Sesame model + its 3 built-in voices (Sadie/Manny/
# Ben) — too large (~1.6GB) to bundle in the app itself, so it's downloaded on demand the first
# time a valid Sesame license key is entered. See sesame_download.py.
SESAME_ASSETS_DIR = os.path.join(os.path.dirname(CONFIG_PATH), "sesame_assets")


def sesame_voices_path(filename):
    os.makedirs(SESAME_VOICES_DIR, exist_ok=True)
    return os.path.join(SESAME_VOICES_DIR, filename)


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(config):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f)
    os.chmod(CONFIG_PATH, 0o600)
