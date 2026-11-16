#!/bin/bash
# Uruchamia aplikację webową w tle przy każdym logowaniu (macOS launchd) i pilnuje, by działała.
# Aplikacja sama pobiera ogłoszenia raz dziennie (godzina w Ustawieniach). Adres: http://localhost:8330
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="pl.bmw330i.webapp"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$DIR/logs"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/python3</string><string>$DIR/webapp.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/logs/webapp.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/webapp.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "OK: aplikacja działa w tle -> http://localhost:8330"
