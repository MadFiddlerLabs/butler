#!/bin/bash
# The daily schedule in the GUI launchd domain (gui/<uid>), as a student gets it once they're
# logged in: launchd/install.sh, unchanged, loads the real jobs; the tick runs at load and again
# when kickstarted, exits cleanly and writes fresh lines to logs/butler.log; uninstall.sh takes
# them out. On a hosted runner only the runner's account (an admin) has a GUI domain, so this
# runs as it. standard-user.sh runs the same jobs as a standard account, in its background domain.
#
#   tests/e2e/schedule-gui.sh        (needs ~/Butler with its Python and config/settings.yaml)
set -euo pipefail
b="$HOME/Butler"
uid="$(id -u)"
fail() {
  echo "::error::schedule (gui/$uid): $*"
  for f in butler.log launchd.err.log launchd.out.log; do echo "--- logs/$f"; tail -n 40 "$b/logs/$f" 2>&1 || true; done
  exit 1
}
within() { local n="$1"; shift; for _ in $(seq 1 "$n"); do "$@" && return 0; sleep 1; done; return 1; }
lines() { { cat "$b/logs/butler.log" 2>/dev/null || true; } | wc -l | tr -d " "; }
job() { launchctl print "gui/$uid/com.buildyourbutler.butler"; }
ran() {  # ran <n>: the tick has run at least n times and isn't running now
  local out runs; out="$(job)"
  runs="$(printf '%s\n' "$out" | sed -n 's/^[[:space:]]*runs = //p' | head -n 1)"
  [ "${runs:-0}" -ge "$1" ] && [ "$(printf "%s\n" "$out" | grep -m 1 -E "^[[:space:]]*state = ")" = "$(printf "\tstate = not running")" ] \
    && printf "%s\n" "$out" | grep -Eq "last exit code = [0-9]+"
}
exit_code() { job | sed -n 's/^[[:space:]]*last exit code = //p' | head -n 1; }

[ -x "$b/.venv/bin/python" ] && [ -f "$b/config/settings.yaml" ] || fail "no Butler with settings in $b"
# The ticks must not reach the real update list: they'd install a real release over a test build.
grep -q "^updates:" "$b/config/settings.yaml" && sed -i '' 's/^updates:.*/updates: off/' "$b/config/settings.yaml" \
  || printf 'updates: off\n' >> "$b/config/settings.yaml"
before="$(lines)"
"$b/launchd/install.sh" || fail "launchd/install.sh failed"
for label in com.buildyourbutler.butler com.buildyourbutler.morning com.buildyourbutler.portal; do
  launchctl print "gui/$uid/$label" >/dev/null || fail "$label isn't loaded"
done
within 300 ran 1 || { job || true; fail "the tick didn't run at load"; }
[ "$(exit_code)" = 0 ] || fail "the tick at load exited with $(exit_code)"
[ "$(lines)" -gt "$before" ] || fail "the tick at load wrote nothing to logs/butler.log"
tail -n +"$((before + 1))" "$b/logs/butler.log"
before="$(lines)"
launchctl kickstart -p "gui/$uid/com.buildyourbutler.butler" || fail "kickstart failed"
within 300 ran 2 || { job || true; fail "the kickstarted tick didn't finish"; }
[ "$(exit_code)" = 0 ] || fail "the kickstarted tick exited with $(exit_code)"
[ "$(lines)" -gt "$before" ] || fail "the kickstarted tick wrote nothing to logs/butler.log"
tail -n +"$((before + 1))" "$b/logs/butler.log"
"$b/launchd/uninstall.sh"
if launchctl print "gui/$uid/com.buildyourbutler.butler" >/dev/null 2>&1; then fail "the tick is still loaded"; fi
echo
echo "the schedule in the GUI domain: all good"
