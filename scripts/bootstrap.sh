#!/bin/bash
# Sets up Butler's Python: a virtualenv in .venv with the packages from requirements.txt.
# uv provides Python itself, so this needs no Xcode tools. The Calendar and Reminders helper
# is built later, by `/butler-setup calendar` (helpers/butler-ek/build.sh). Safe to run again.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

uv="$(command -v uv || true)"
[ -n "$uv" ] || [ ! -x "$HOME/.local/bin/uv" ] || uv="$HOME/.local/bin/uv"
if [ -z "$uv" ]; then
  echo "Installing uv, which gives Butler its own Python (https://docs.astral.sh/uv/)."
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
  uv="$HOME/.local/bin/uv"
fi

[ -x .venv/bin/python ] || "$uv" venv --quiet --python 3.13 .venv
"$uv" pip install --quiet --python .venv/bin/python -r requirements.txt
mkdir -p data logs out
echo "Python is ready. Next: ./run.sh preview"
