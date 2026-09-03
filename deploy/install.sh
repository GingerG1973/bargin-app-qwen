#!/bin/bash
# Install launchd agent for Bargin

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_NAME="com.bargin.watcher"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

# Create LaunchAgents directory if needed
mkdir -p "$HOME/Library/LaunchAgents"

# Generate the plist file
cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>
    
    <key>ProgramArguments</key>
    <array>
        <string>${SCRIPT_DIR}/../.venv/bin/python</string>
        <string>-m</string>
        <string>bargin.cli</string>
        <string>run</string>
    </array>
    
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}/..</string>
    
    <key>StandardOutPath</key>
    <string>${SCRIPT_DIR}/../data/watcher.log</string>
    
    <key>StandardErrorPath</key>
    <string>${SCRIPT_DIR}/../data/watcher.log</string>
    
    <key>StartInterval</key>
    <integer>900</integer>
    
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
EOF

# Load the agent
launchctl bootout gui/$(id -u)/$PLIST_NAME 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$PLIST_PATH"

echo "✓ Installed launchd agent: $PLIST_NAME"
echo ""
echo "Commands:"
echo "  launchctl kickstart -k gui/$(id -u)/$PLIST_NAME   # Run now"
echo "  tail -f ${SCRIPT_DIR}/../data/watcher.log         # View logs"
echo "  launchctl bootout gui/$(id -u)/$PLIST_NAME        # Remove"
