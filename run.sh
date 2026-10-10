#!/bin/bash
# Entry point for people and launchd.
#   ./run.sh preview                 # build this morning's brief and open it; sends nothing
#   ./run.sh daily [--dry-run] [--no-llm] [--date YYYY-MM-DD]
set -euo pipefail
cd "$(dirname "$0")"
cmd="${1:-daily}"; shift || true
# preview: this morning's brief, built but not sent, opened in the browser. Until there's a
# Claude key in Keychain, it leaves out the parts Claude writes.
if [ "$cmd" = "preview" ]; then
  nollm=""
  security find-generic-password -s butler-anthropic >/dev/null 2>&1 || nollm="--no-llm"
  PYTHONPATH=src .venv/bin/python -m butler.daily --dry-run $nollm "$@"
  latest="$(ls -t out/preview/brief-*.html | head -n 1)"
  [ -n "${BUTLER_NO_OPEN:-}" ] || open "$latest"
  exit 0
fi
# The scheduled jobs hold off idle sleep while they run (a 07:00 wake can otherwise
# drop back to sleep mid-send). The portal runs plain.
if [ "$cmd" = "tick" ]; then
  PYTHONPATH=src exec /usr/bin/caffeinate -i .venv/bin/python -m "butler.$cmd" "$@"
fi
PYTHONPATH=src exec .venv/bin/python -m "butler.$cmd" "$@"
