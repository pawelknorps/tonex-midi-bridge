#!/bin/bash
# Install/uninstall a macOS LaunchAgent that keeps tonex-midi-bridge alive.
# Usage: scripts/install-launchagent.sh [uninstall]
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.pawelknorps.tonex-bridge"
PLIST_SRC="$DIR/scripts/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/tonex-bridge.log"
ERR="$HOME/Library/Logs/tonex-bridge.err.log"

if [ "${1:-}" = "uninstall" ]; then
    launchctl bootout "gui/$(id -u)" "$PLIST_DST" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "removed $PLIST_DST"
    exit 0
fi

if [ ! -x "$DIR/.venv/bin/python" ]; then
    echo "venv missing — run: cd $DIR && python3 -m venv .venv && .venv/bin/pip install pyserial mido python-rtmidi" >&2
    exit 1
fi

mkdir -p "$(dirname "$LOG")"
sed -e "s|__PYTHON__|$DIR/.venv/bin/python|" \
    -e "s|__BRIDGE__|$DIR/tonex_bridge.py|" \
    -e "s|__LOG__|$LOG|" \
    -e "s|__ERR__|$ERR|" "$PLIST_SRC" > "$PLIST_DST"

launchctl bootout "gui/$(id -u)" "$PLIST_DST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
echo "installed $PLIST_DST"
echo "logs: $LOG (+ $ERR); uninstall: $0 uninstall"