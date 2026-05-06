#!/bin/bash
# Remove all ai-digest launchd jobs. Safe to re-run.

set -e

LA_DIR="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"

for name in fetch radar digest; do
  label="com.txie.ai-digest.${name}"
  plist="${LA_DIR}/${label}.plist"
  if [ -f "$plist" ]; then
    launchctl bootout "${DOMAIN}/${label}" 2>/dev/null || true
    rm "$plist"
    echo "✓ removed ${label}"
  else
    echo "  (skip) ${label} not installed"
  fi
done
