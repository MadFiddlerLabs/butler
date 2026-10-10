#!/bin/bash
# Stops and removes every Butler launchd job. Your config and data stay where they are.
set -uo pipefail
for label in com.buildyourbutler.butler com.buildyourbutler.morning com.buildyourbutler.portal com.buildyourbutler.caddy com.buildyourbutler.cert; do
  target="$HOME/Library/LaunchAgents/$label.plist"
  [ -f "$target" ] || continue
  launchctl bootout "gui/$(id -u)" "$target" 2>/dev/null || true
  rm "$target"
  echo "removed $target"
done
