#!/bin/bash
# Registers the SonoScript native-messaging host with Chrome and Edge on macOS.
# Safe to re-run any time (e.g. after moving the repo) — always overwrites with the current path.
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$DIR/native_host.py"

CHROME_DIR="$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts"
EDGE_DIR="$HOME/Library/Application Support/Microsoft Edge/NativeMessagingHosts"

for TARGET_DIR in "$CHROME_DIR" "$EDGE_DIR"; do
  mkdir -p "$TARGET_DIR"
  sed "s|__ABSOLUTE_PATH_TO_native_host.py__|$DIR/native_host.py|" \
    "$DIR/com.sonoscript.bridge.json" > "$TARGET_DIR/com.sonoscript.bridge.json"
  echo "Registered for: $TARGET_DIR"
done
