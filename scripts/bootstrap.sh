#!/bin/bash
# One-time setup: a Python virtualenv with the dependencies, and the Calendar/Reminders helper.
# Safe to run again.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

python=""
for candidate in python3.14 python3.13 python3.12 python3; do
  if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    python="$candidate"; break
  fi
done
if [ -z "$python" ]; then
  echo "Butler needs Python 3.12 or later. Install it with: brew install python" >&2; exit 1
fi
if ! command -v swiftc >/dev/null; then
  echo "Butler needs the Swift compiler. Install it with: xcode-select --install" >&2; exit 1
fi

[ -d .venv ] || "$python" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
helpers/butler-ek/build.sh
mkdir -p data logs out
echo "ready. next: ./run.sh daily --dry-run --no-llm"
