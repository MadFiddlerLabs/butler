#!/bin/bash
# Renews your domain's certificate when it's within 30 days of expiry, then has Caddy pick it
# up. Run daily by launchd (com.buildyourbutler.cert). DOMAIN and EMAIL come from caddy/domain.env, which
# install.sh writes. See docs/PORTAL.md for the one-time setup.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
# shellcheck source=/dev/null
source caddy/domain.env
cert="certs/certificates/$DOMAIN.crt"
before=$(shasum "$cert" | cut -d' ' -f1)
ACME_DNS_API_BASE=https://auth.acme-dns.io ACME_DNS_STORAGE_PATH=certs/acme-dns.json \
  /opt/homebrew/bin/lego run --accept-tos --email "$EMAIL" --dns acmedns \
  --dns.resolvers 1.1.1.1:53 --domains "$DOMAIN" --path certs --renew-days 30
after=$(shasum "$cert" | cut -d' ' -f1)
if [ "$before" != "$after" ]; then
  /opt/homebrew/bin/caddy reload --config caddy/Caddyfile.local --adapter caddyfile --force
  echo "$(date) renewed and reloaded"
fi
