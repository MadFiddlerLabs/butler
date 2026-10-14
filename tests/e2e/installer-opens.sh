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
#   3. the Butler app is in /Applications (this runner's account is an admin, so the command-line
#      install moved it there) and nowhere else; opened, it asks Terminal to run
#      scripts/butler.command, which runs `butler resume`, which resumes the unfinished first run
#   4. with the first run finished, the app opens Claude Code (a stub that records how it was
#      started) in the Butler folder, in the household's mode, on Module 1's welcome
# What it can't: Installer.app itself (it needs a click), a Mac where the student isn't the
# console user or isn't an admin (tests/test_packaging.py covers those with fakes), and the real
# Claude Code. Those paths run the same postinstall and `butler resume`, and logs/install.log and
# logs/setup.log say what happened.
#
#   tests/e2e/installer-opens.sh <the installed .pkg>
set -euo pipefail
pkg="$1"
here="$(cd "$(dirname "$0")" && pwd)"
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

step "the Butler app is in /Applications, and opens Terminal on butler resume"
app="/Applications/Butler.app"
[ -x "$app/Contents/MacOS/Butler" ] || fail "no Butler app in /Applications"
[ ! -e "$HOME/Applications/Butler.app" ] || fail "a second Butler app is left in ~/Applications"
[ "$(stat -f %Su "$app")" = "$(id -un)" ] || fail "the app in /Applications isn't the student's"
[ "$(plutil -extract CFBundleIdentifier raw -o - "$app/Contents/Info.plist")" = com.buildyourbutler.butler-app ] \
  || fail "the app in /Applications isn't ours"
codesign --verify --strict "$app" || fail "the Butler app's signature doesn't verify after the move"
grep -q "the Butler app is in /Applications (moved, from ~/Applications)" "$b/logs/install.log" \
  || fail "install.log doesn't say where the app went"
open "$app"
within 60 pgrep -f "Butler/scripts/butler.command" >/dev/null || fail "the app didn't open butler.command in Terminal"
within 60 grep -q "resume: the first run" "$b/logs/setup.log" || fail "butler resume didn't resume the first run"
pgrep -x Terminal >/dev/null || fail "Terminal isn't running"

step "with the first run finished, the app opens Claude Code on the next lesson"
pkill -x Terminal || true
within 10 bash -c '! pgrep -x Terminal >/dev/null' || true
# The first runs above started Python's setup in the background; finish it here, on its own.
pkill -f "$b/scripts/bootstrap.sh" || true
pkill -f "$b/bin/uv" || true
"$b/scripts/bootstrap.sh" >/dev/null
(cd "$b" && .venv/bin/python "$here/install_step_settings.py")
printf 'claude_mode: ask\n' >> "$b/config/settings.yaml"
date -u +%Y-%m-%dT%H:%M:%SZ > "$b/data/first-run-done"
# A stand-in for Claude Code, found first by Terminal's login shell, whichever shell that is.
stub="$work/claude-bin"
mkdir -p "$stub"
printf '#!/bin/bash\n{ pwd; printf "%%s\\n" "$@"; } > "%s"\n' "$work/claude-launched.txt" > "$stub/claude"
chmod +x "$stub/claude"
for profile in .zprofile .bash_profile; do printf '\nexport PATH="%s:$PATH"\n' "$stub" >> "$HOME/$profile"; done
open "$app"
within 90 test -s "$work/claude-launched.txt" || fail "the app didn't open Claude Code"
cat "$work/claude-launched.txt"
[ "$(sed -n 1p "$work/claude-launched.txt")" = "$(cd "$b" && pwd -P)" ] || [ "$(sed -n 1p "$work/claude-launched.txt")" = "$b" ] \
  || fail "Claude Code didn't start in the Butler folder"
[ "$(sed -n 2,3p "$work/claude-launched.txt" | tr '\n' ' ')" = "--permission-mode default " ] \
  || fail "Claude Code didn't start in the household's mode"
sed -n 4p "$work/claude-launched.txt" | grep -q "Module 1 of the Build Your Butler course, Meet Claude Code" \
  || fail "Claude Code didn't start on Module 1's welcome"
grep -q "resume: Claude Code, Module 1" "$b/logs/setup.log" || fail "setup.log doesn't say what the app opened"
test -f "$b/data/claude-welcomed" || fail "the welcome isn't marked done"
pkill -x Terminal || true

echo
echo "installer opens Terminal: all good"
