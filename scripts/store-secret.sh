#!/bin/bash
# Asks for a secret in a Mac dialog and stores it in the login Keychain. The secret goes from
# the dialog straight to Keychain: it never appears in a chat, a file or this script's output.
#
#   scripts/store-secret.sh butler-gmail "Your butler's Gmail app password"
#
# Only Butler's own Keychain entries are accepted. Spaces are removed, because Google shows app
# passwords in groups of four. The secret never goes on a command line (scripts/lib/keychain.sh).
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=lib/keychain.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/keychain.sh"
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
printf '%s' "$secret" | keychain_store "$service" || { echo "Keychain refused it: nothing stored"; exit 1; }
echo "stored $service in your Keychain"
