#!/bin/bash
# Stops and removes every Casa launchd job. Your config and data stay where they are.
set -uo pipefail
for label in com.casa com.casa.morning com.casa.portal com.casa.caddy com.casa.cert; do
  target="$HOME/Library/LaunchAgents/$label.plist"
  [ -f "$target" ] || continue
  launchctl bootout "gui/$(id -u)" "$target" 2>/dev/null || true
  rm "$target"
  echo "removed $target"
done
