#!/bin/bash
# The installer's last act, run the way Installer.app runs it: the package's postinstall, with
# COMMAND_LINE_INSTALL unset, in the logged-in user's session (a macOS runner has one). `installer`
# on the command line can't show this: it sets COMMAND_LINE_INSTALL, so Terminal never opens.
#
# What it proves, after `installer` put the package in this user's home:
#   1. a fresh install asks Terminal to open first-run.command, through launchctl asuser, and
#      Terminal really runs it: first-run.sh's first act writes data/first-run-started
#   2. over a first run that began, it opens welcome-back.command instead, and Terminal runs it:
#      first-run.sh --again logs "welcome back"
#   3. the Butler app in ~/Applications opens Terminal on scripts/butler.command
# What it can't: Installer.app itself (it needs a click), and a Mac where the student isn't the
# console user. Those paths run the same postinstall, and logs/install.log says what happened.
#
#   tests/e2e/installer-opens.sh <the installed .pkg>
set -euo pipefail
pkg="$1"
b="$HOME/Butler"
work="$(mktemp -d)"
trap 'pkill -x Terminal 2>/dev/null || true; rm -rf "$work"' EXIT
fail() { echo "FAIL: $*" >&2; tail -n 20 "$b/logs/install.log" "$b/logs/setup.log" >&2 2>/dev/null || true; exit 1; }
step() { echo; echo "== $*"; }
within() {  # within <seconds> <command…>: true once the command succeeds
  local n="$1"; shift
  for _ in $(seq 1 "$n"); do "$@" && return 0; sleep 1; done
  return 1
}
postinstall() {
  (cd "$work/expanded/Butler-component.pkg/Scripts" && env -u COMMAND_LINE_INSTALL ./postinstall "$pkg" "$HOME")
}
pkgutil --expand "$pkg" "$work/expanded"

step "the command-line install opened nothing, and said so"
grep -q "from the command line: Terminal not opened" "$b/logs/install.log" || fail "no command-line line in install.log"
[ "$(cd "$b" && bash -c '. scripts/lib/install.sh; install_state .')" = fresh ] || fail "the folder isn't fresh"

step "a fresh install opens the first run in Terminal"
postinstall
tail -n 2 "$b/logs/install.log"
grep -q "installed (fresh): opened Terminal with first-run.command (launchctl asuser" "$b/logs/install.log" \
  || fail "postinstall didn't open Terminal through launchctl asuser"
within 60 test -f "$b/data/first-run-started" || fail "Terminal didn't run the first run"
pgrep -x Terminal >/dev/null || fail "Terminal isn't running"
grep -qF "export PATH=\"$b/bin:\$PATH\"" "$HOME/.zprofile" || fail "butler isn't on the PATH for new windows"

step "over a first run that began, it opens the choice to resume or start fresh"
pkill -x Terminal || true
within 10 bash -c '! pgrep -x Terminal >/dev/null' || true
postinstall
tail -n 1 "$b/logs/install.log"
grep -q "installed (started): opened Terminal with welcome-back.command (launchctl asuser" "$b/logs/install.log" \
  || fail "postinstall didn't open the welcome back"
within 60 grep -q "welcome back" "$b/logs/setup.log" || fail "Terminal didn't run the welcome back"
pkill -x Terminal || true

step "the Butler app opens Terminal on butler"
app="$HOME/Applications/Butler.app"
[ -x "$app/Contents/MacOS/Butler" ] || fail "no Butler app in ~/Applications"
codesign --verify --strict "$app" || fail "the Butler app's signature doesn't verify"
open "$app"
within 60 pgrep -f "Butler/scripts/butler.command" >/dev/null || fail "the app didn't open butler.command in Terminal"

echo
echo "installer opens Terminal: all good"
