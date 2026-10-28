#!/bin/bash
# A persona run on a Mac, as the student's own account: "BYB Test" (account bybtest), a standard
# account, not an admin, with a space in its home folder's path ("/Users/byb test"), as
# tests/e2e/standard-user.sh makes it. The runner's admin only makes the account, hands it the
# harness, the package and the lessons, and does the one admin step the course has (admin.py);
# everything the student does runs as the account, from a clean environment.
#
# What a hosted runner can't give the account, and what stands in (run.json's `differences`
# lists what each run met):
#   - A screen session: the runner's admin is the only one at the screen. So Installer can't
#     install into the account's home and Terminal can't open for it (run_persona.install_mac);
#     launchd's GUI domain doesn't exist, so the jobs go into the account's own background domain
#     (stubs/launchctl.py, as standard-user.sh does).
#   - A login keychain: macOS makes one at the first login at the screen. This makes it the same
#     way, empty and unlocked, as after a student's first login, so Butler's sign-in and keys go
#     into Keychain as they would.
#
#   tests/personas/as-student.sh <out folder> -- <run_persona.py arguments, without --out>
#   (ANTHROPIC_API_KEY in the environment for a paid run; none for --rehearse)
set -euo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd)"
out="$1"; shift
[ "${1:-}" = "--" ] && shift
student="bybtest"
home="/Users/byb test"
shared="/Users/Shared/byb-persona"
step() { echo; echo "== $*"; }

step "a standard account, not an admin, with a space in its home folder's path"
password="$(openssl rand -base64 24)"    # never printed; nobody logs in with it
sudo sysadminctl -addUser "$student" -fullName "BYB Test" -password "$password" -home "$home" >/dev/null 2>&1
unset password
[ -d "$home" ] || sudo createhomedir -c -u "$student" >/dev/null
if dseditgroup -o checkmember -m "$student" admin >/dev/null 2>&1; then echo "::error::$student is an admin"; exit 1; fi
echo "$student: uid $(id -u "$student"), home \"$home\", standard (not an admin)"

step "its login keychain, as macOS makes one at the first login"
sudo -u "$student" -H bash -c 'mkdir -p ~/Library/Keychains && cd ~/Library/Keychains &&
  security create-keychain -p "" login.keychain-db &&
  security list-keychains -d user -s login.keychain-db &&
  security default-keychain -d user -s login.keychain-db &&
  security login-keychain -d user -s login.keychain-db &&
  security set-keychain-settings login.keychain-db &&
  security default-keychain -d user'

step "the harness, its Python, the package and the lessons, where the account can read them"
sudo rm -rf "$shared"
sudo mkdir -p "$shared/admin" "$shared/out" "$shared/secret"
sudo chown "$(id -un)" "$shared"
rsync -a --exclude .git --exclude .venv --exclude dist "$repo/" "$shared/engine/"
# shellcheck source=../../scripts/lib/uv.sh
. "$repo/scripts/lib/uv.sh"
uv="$(butler_uv "$shared/bin")"
UV_PYTHON_INSTALL_DIR="$shared/python" "$uv" venv --quiet --python 3.13 --managed-python "$shared/venv"
"$uv" pip install --quiet --python "$shared/venv/bin/python" anthropic==2.3.0 claude-agent-sdk==0.2.174 pyyaml tzdata==2026.5
sudo chmod -R a+rX "$shared"
sudo chmod 1777 "$shared/admin"
sudo chown "$student" "$shared/out" "$shared/secret"
sudo chmod 700 "$shared/secret"
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  printf '%s' "$ANTHROPIC_API_KEY" | sudo -u "$student" tee "$shared/secret/key" >/dev/null
  sudo chmod 600 "$shared/secret/key"
fi

step "the admin step the course has (Tailscale's install), for the length of the run"
sudo "$shared/venv/bin/python" "$shared/engine/tests/personas/admin.py" serve "$shared/admin" > "$shared/admin.log" 2>&1 &
admin_pid=$!
trap 'sudo kill "$admin_pid" 2>/dev/null || true' EXIT

step "the persona, as $student"
cd "$shared"
code=0
# A clean environment, as a login gives: nothing of the runner's. The keychain is unlocked in the
# same session as the harness, as a login unlocks it.
sudo -u "$student" -H env -i HOME="$home" USER="$student" LOGNAME="$student" SHELL=/bin/zsh \
  PATH=/usr/bin:/bin:/usr/sbin:/sbin TERM=xterm-256color LANG=en_US.UTF-8 CI=1 BYB_ADMIN_REQUESTS="$shared/admin" \
  /bin/bash -c 'security unlock-keychain -p "" login.keychain-db && exec "$@"' bash \
  "$shared/venv/bin/python" -X utf8 "$shared/engine/tests/personas/run_persona.py" "$@" \
  --key-file "$shared/secret/key" --out "$shared/out" || code=$?
sudo rm -f "$shared/secret/key"

step "what the run left"
mkdir -p "$out"
sudo cp -R "$shared/out/." "$out/"
sudo chown -R "$(id -un)" "$out"
echo "--- admin.log"; cat "$shared/admin.log" || true
for f in install.log setup.log butler.log; do
  echo "--- $f"; sudo tail -n 40 "$home/Butler/logs/$f" 2>/dev/null || true
done
exit "$code"
