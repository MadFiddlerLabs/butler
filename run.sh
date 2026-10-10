#!/bin/bash
# Entry point for people and launchd.
#   ./run.sh daily [--dry-run] [--no-llm] [--date YYYY-MM-DD]
set -euo pipefail
cd "$(dirname "$0")"
cmd="${1:-daily}"; shift || true
# The scheduled jobs hold off idle sleep while they run (a 07:00 wake can otherwise
# drop back to sleep mid-send). The portal runs plain.
if [ "$cmd" = "tick" ]; then
  PYTHONPATH=src exec /usr/bin/caffeinate -i .venv/bin/python -m "casa.$cmd" "$@"
fi
PYTHONPATH=src exec .venv/bin/python -m "casa.$cmd" "$@"
