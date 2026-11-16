#!/bin/bash
# Instaluje codzienne uruchamianie trackera (macOS launchd). Domyślnie 08:00.
# Użycie: ./install_schedule.sh [GODZINA] [MINUTA]     np. ./install_schedule.sh 7 30
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
HOUR="${1:-8}"; MIN="${2:-0}"
LABEL="pl.bmw330i.tracker"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$DIR/logs"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/python3</string><string>$DIR/tracker.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer>
  </dict>
  <key>StandardOutPath</key><string>$DIR/logs/tracker.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/tracker.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "OK: tracker uruchamia się codziennie o $HOUR:$(printf %02d $MIN). Raport: $DIR/reports/latest.html"
echo "Jeśli Mac śpi o tej porze, zadanie wykona się po wybudzeniu."
