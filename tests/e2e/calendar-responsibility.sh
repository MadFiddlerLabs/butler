#!/bin/bash
# Who does macOS hold responsible when butler-ek asks for Calendar access? Started from a shell, the
# helper must answer for itself, or a grant made in Terminal wouldn't cover the 07:00 launchd run
# (website #77). tccd logs the request; this reads what it said.
#
#   tests/e2e/calendar-responsibility.sh <path to the built butler-ek>
set -uo pipefail
helper="$1"
log="$(mktemp)"
/usr/bin/log stream --level debug --style compact --predicate 'process == "tccd"' > "$log" 2>&1 &
stream=$!
sleep 3
"$helper" calendars > /dev/null 2>&1 &
ask=$!
sleep 10
kill "$ask" 2>/dev/null
sleep 2
kill "$stream" 2>/dev/null
echo "tccd said $(wc -l < "$log") lines"
grep -iE "butler-ek|responsib|Calendar" "$log" | cut -c1-260 | head -40
