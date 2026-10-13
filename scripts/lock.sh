#!/bin/bash
# Writes requirements.lock from requirements.txt: every package Butler runs on, its dependencies
# included, pinned to one version with the SHA-256 of each of its files on PyPI. bootstrap.sh
# installs exactly that, with hashes required. Run it after changing requirements.txt, and to
# take newer versions on purpose:
#
#   scripts/lock.sh             # keep today's pins where requirements.txt still allows them
#   scripts/lock.sh --upgrade   # the newest versions requirements.txt allows
#   scripts/lock.sh --check     # CI: fail if the lock's pins don't follow from requirements.txt
#
# One lock serves every Mac, Apple silicon and Intel, and CI's Linux: it is resolved for all
# platforms (--universal) from Python 3.12 up (bootstrap.sh's Python is 3.13; CI tests on 3.12).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
# shellcheck source=lib/uv.sh
. "$root/scripts/lib/uv.sh"
uv="$(butler_uv "$root/bin")"

compile() {  # compile <output> [extra uv flags]
  local out="$1"; shift
  "$uv" pip compile --quiet --universal --python-version 3.12 --generate-hashes \
    --custom-compile-command "scripts/lock.sh" "$@" -o "$out" requirements.txt
}

case "${1:-}" in
  --check)
    # The pins only: PyPI can add a file to an old version later, which changes a hash list but
    # not what installs.
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    cp requirements.lock "$tmp/requirements.lock"
    compile "$tmp/requirements.lock"
    pins() { grep -E '^[A-Za-z0-9]' "$1"; }
    if ! diff <(pins requirements.lock) <(pins "$tmp/requirements.lock"); then
      echo "requirements.lock doesn't follow from requirements.txt. Run scripts/lock.sh and commit it." >&2
      exit 1
    fi
    echo "requirements.lock is current." ;;
  --upgrade) compile requirements.lock --upgrade ;;
  "") compile requirements.lock ;;
  *) echo "usage: scripts/lock.sh [--check | --upgrade]" >&2; exit 2 ;;
esac
