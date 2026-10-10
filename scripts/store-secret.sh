#!/bin/bash
# Asks for a secret in a Mac dialog and stores it in the login Keychain. The secret goes from
# the dialog straight to Keychain: it never appears in a chat, a file or this script's output.
#
#   scripts/store-secret.sh butler-gmail "Your butler's Gmail app password"
#
# Only Butler's own Keychain entries are accepted. Spaces are removed, because Google shows app
# passwords in groups of four.
set -euo pipefail
service="${1:-}"; label="${2:-Paste the secret}"
case "$service" in
  butler-gmail|butler-anthropic|butler-lunchmoney) ;;
  *) echo "usage: $0 butler-gmail|butler-anthropic|butler-lunchmoney \"what to paste\"" >&2; exit 2 ;;
esac
label="${label//\"/}"
secret="$(osascript -e "text returned of (display dialog \"$label\" with title \"Butler\" default answer \"\" with hidden answer buttons {\"Cancel\", \"Save\"} default button \"Save\")" 2>/dev/null)" \
  || { echo "cancelled: nothing stored"; exit 1; }
secret="$(printf '%s' "$secret" | tr -d '[:space:]')"
[ -n "$secret" ] || { echo "the box was empty: nothing stored"; exit 1; }
security add-generic-password -U -s "$service" -a butler -w "$secret"
echo "stored $service in your Keychain"
