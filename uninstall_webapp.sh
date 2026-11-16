#!/bin/bash
launchctl bootout "gui/$(id -u)/pl.bmw330i.webapp" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/pl.bmw330i.webapp.plist"
echo "Zatrzymano aplikację webową w tle."
