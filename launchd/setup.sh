#!/bin/bash
# Generates the launchd plist and installs it for the current user.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE="$SCRIPT_DIR/com.runcoach.plist.template"
PLIST_NAME="com.runcoach.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"

# Find required tools
UV_PATH="$(which uv 2>/dev/null || true)"
CLAUDE_PATH="$(which claude 2>/dev/null || true)"

if [ -z "$UV_PATH" ]; then
    echo "Error: 'uv' not found. Install it first: https://docs.astral.sh/uv/"
    exit 1
fi

if [ -z "$CLAUDE_PATH" ]; then
    echo "Warning: 'claude' CLI not found in PATH. The bot may fail if it needs Claude."
    CLAUDE_DIR="/usr/local/bin"
else
    CLAUDE_DIR="$(dirname "$CLAUDE_PATH")"
fi

UV_DIR="$(dirname "$UV_PATH")"

echo "Project directory: $PROJECT_DIR"
echo "uv: $UV_PATH"
echo "claude: ${CLAUDE_PATH:-not found}"

# Generate plist from template
sed \
    -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    -e "s|__UV_PATH__|$UV_PATH|g" \
    -e "s|__UV_DIR__|$UV_DIR|g" \
    -e "s|__CLAUDE_DIR__|$CLAUDE_DIR|g" \
    "$TEMPLATE" > "$PLIST_DEST"

echo "Installed plist to: $PLIST_DEST"

# Unload if already loaded, then load
launchctl bootout gui/$(id -u) "$PLIST_DEST" 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$PLIST_DEST"

echo "RunCoach service started. Check logs at: $PROJECT_DIR/data/"
