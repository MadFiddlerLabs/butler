#!/bin/bash
# Sets up Butler's Python: a virtualenv in .venv holding exactly the packages in requirements.lock.
# uv provides Python itself, so this needs no Xcode tools. The Calendar and Reminders helper
# is built later, by `/butler-setup calendar` (helpers/butler-ek/build.sh). Safe to run again:
# every update runs it.
#
# requirements.lock pins every package, its dependencies included, with SHA-256 hashes, so each
# install of a version gets the packages its tests ran with, and a changed upload on PyPI is
# refused. Only prebuilt wheels are installed: nothing is compiled, and no build tool runs
# unchecked. `uv pip sync` also removes anything the lock doesn't list. uv itself is pinned by
# version and checksum (scripts/lib/uv.sh). docs/UPDATES.md has the details.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
# shellcheck source=lib/uv.sh
. "$root/scripts/lib/uv.sh"

uv="$(butler_uv "$root/bin")"
[ -x .venv/bin/python ] || "$uv" venv --quiet --python 3.13 .venv
"$uv" pip sync --quiet --python .venv/bin/python --require-hashes --no-build requirements.lock
mkdir -p data logs out
# This Mac account only: other accounts can open a home folder's subfolders, and this one holds
# the household's notes, money and the reference file with its ID numbers.
chmod 700 "$root"
echo "Python is ready. Next: ./run.sh preview"
