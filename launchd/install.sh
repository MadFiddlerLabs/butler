#!/bin/bash
# Installs (or reinstalls) the launchd jobs.
#
#   ./launchd/install.sh
#       The 5-minute tick, the send-time trigger and the always-on portal (127.0.0.1 only).
#
#   ./launchd/install.sh --domain casa.example.com --tailnet-host my-mac.my-tailnet.ts.net --email you@example.com
#       Also Caddy and a daily certificate renewal, to serve the portal under your own domain
#       to your tailnet. See docs/PORTAL.md first: it needs DNS records and a one-time lego run.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
domain="" host="" email=""
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) domain="$2"; shift 2 ;;
    --tailnet-host) host="$2"; shift 2 ;;
    --email) email="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$root/logs"
read -r send_hour weekly_hour < <(cd "$root" && PYTHONPATH=src .venv/bin/python -c \
  "from casa.config import load_settings as l; e = l()['email']; print(e['send_hour'], e['weekly_hour'])")

labels="com.casa com.casa.morning com.casa.portal"
if [ -n "$domain" ]; then
  if [ -z "$host" ] || [ -z "$email" ]; then
    echo "--domain needs --tailnet-host and --email too" >&2; exit 2
  fi
  sed -e "s#__ROOT__#$root#g" -e "s#__DOMAIN__#$domain#g" -e "s#__TAILNET_HOST__#$host#g" \
    "$root/caddy/Caddyfile" > "$root/caddy/Caddyfile.local"
  printf 'DOMAIN=%s\nEMAIL=%s\n' "$domain" "$email" > "$root/caddy/domain.env"
  labels="$labels com.casa.caddy com.casa.cert"
fi

for label in $labels; do
  target="$HOME/Library/LaunchAgents/$label.plist"
  sed -e "s#__ROOT__#$root#g" -e "s#__SEND_HOUR__#$send_hour#g" -e "s#__WEEKLY_HOUR__#$weekly_hour#g" \
    "$root/launchd/$label.plist" > "$target"
  launchctl bootout "gui/$(id -u)" "$target" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$target"
  echo "installed $target"
done
echo "watch it with: tail -f $root/logs/casa.log"
