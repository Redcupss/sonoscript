import json
import os

CONFIG_PATH = os.path.expanduser("~/Library/Application Support/SonoScript/config.json")


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
