#!/bin/bash
launchctl bootout "gui/$(id -u)/pl.bmw330i.tracker" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/pl.bmw330i.tracker.plist"
echo "Usunięto codzienne uruchamianie."
