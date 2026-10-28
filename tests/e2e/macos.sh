#!/bin/bash
# End to end on a Mac, the way a student meets Butler in course Modules 2 and 3: install, first
# preview, then the Calendar helper build. Needs no accounts and sends nothing.
#
#   tests/e2e/macos.sh            # installs this checkout (BUTLER_SOURCE)
#   tests/e2e/macos.sh --hosted   # runs the real one-line installer against GitHub
set -euo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
export BUTLER_HOME="$work/Butler" BUTLER_NO_OPEN=1
fail() { echo "FAIL: $*" >&2; exit 1; }
step() { echo; echo "== $*"; }

step "install"
if [ "${1:-}" = "--hosted" ]; then
  curl -fsSL https://raw.githubusercontent.com/MadFiddlerLabs/butler/main/scripts/install.sh | bash
else
  BUTLER_SOURCE="$repo" "$repo/scripts/install.sh"
fi
cd "$BUTLER_HOME"
[ -x .venv/bin/python ] || fail "no Python in .venv"
.venv/bin/python -c 'import sys; assert sys.version_info >= (3, 12), sys.version'
[ -f .butler-version ] || fail "no .butler-version"

step "Module 2: first preview on the example household"
./run.sh preview > "$work/preview-example.log" 2>&1 || { cat "$work/preview-example.log"; fail "preview failed"; }
grep -q "Traceback" "$work/preview-example.log" && { cat "$work/preview-example.log"; fail "traceback in preview"; }

step "Module 2: the install step's settings, then the preview"
.venv/bin/python "$repo/tests/e2e/install_step_settings.py"
PYTHONPATH=src .venv/bin/python -m unittest discover -s "$repo/tests" -p test_config.py
rm -rf out/preview
./run.sh preview > "$work/preview.log" 2>&1 || { cat "$work/preview.log"; fail "preview failed"; }
html="$(ls out/preview/brief-*.html)"
grep -q "Traceback" "$work/preview.log" && { cat "$work/preview.log"; fail "traceback in preview"; }
grep -q "Bento" "$html" || fail "the butler's name isn't in the brief"
grep -q "Not connected yet: calendar, money, to-dos." "$html" || fail "no 'Not connected yet' line"
grep -q "Problems this morning" "$html" && fail "the preview reports problems"
grep -q "Next up" "$html" && fail "example projects shown to a real household"
grep -q "ts.net" "$html" && fail "the brief links to the example portal"
[ ! -e projects ] || [ -z "$(ls projects)" ] || fail "example projects were copied"

step "Running the installer again keeps the household's files"
BUTLER_SOURCE="${BUTLER_SOURCE_AGAIN:-$repo}" "$repo/scripts/install.sh" > /dev/null
grep -q "^name: Bento" config/settings.yaml || fail "reinstall changed settings.yaml"

step "Module 3: build the Calendar helper"
helpers/butler-ek/build.sh
signature="$(codesign -dv bin/butler-ek 2>&1)"
[[ "$signature" == *"Identifier=com.buildyourbutler.butler-ek"* ]] || fail "wrong signing identifier"
for cmd in delete-event update-event rename-reminder; do
  if bin/butler-ek "$cmd" --id x 2> "$work/ek.err"; then fail "butler-ek accepted $cmd"; fi
  grep -q "unknown command" "$work/ek.err" || fail "butler-ek $cmd: unexpected error"
done
# The helper answers for its own Calendar access (docs/SETUP.md): it re-runs itself once with
# responsibility disclaimed, and the second run must behave exactly like the first.
strings bin/butler-ek | grep -q responsibility_spawnattrs_setdisclaim || fail "butler-ek doesn't disclaim"
if BUTLER_EK_DISCLAIMED=1 bin/butler-ek delete-event 2> "$work/ek.err"; then fail "butler-ek accepted delete-event"; fi
grep -q "unknown command" "$work/ek.err" || fail "butler-ek, already disclaimed: unexpected error"

step "Secrets helper refuses anything but Butler's own entries"
if scripts/store-secret.sh some-other-service "x" 2>/dev/null; then fail "store-secret accepted another service"; fi

step "launchd templates are valid property lists"
for plist in launchd/*.plist; do
  sed -e "s#__ROOT__#$BUTLER_HOME#g" -e "s#__SEND_HOUR__#7#g" -e "s#__WEEKLY_HOUR__#17#g" "$plist" > "$work/check.plist"
  plutil -lint "$work/check.plist" > /dev/null || fail "$plist isn't a valid plist"
  grep -q "__" "$work/check.plist" && fail "$plist has an unfilled placeholder"
done
plutil -lint helpers/butler-ek/Info.plist > /dev/null

echo
echo "macOS end to end: all good"
