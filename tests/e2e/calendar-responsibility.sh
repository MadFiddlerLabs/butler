#!/bin/bash
# Who does macOS hold responsible when butler-ek asks for Calendar access? Started from a shell, the
# helper must answer for itself, or a grant made in Terminal wouldn't cover the 07:00 launchd run
# (website #77; helpers/butler-ek/main.swift disclaims responsibility for this). tccd logs each
# request as "Sub:{...}Resp:{...}", and Resp is the process the grant belongs to. It asks for real,
# so it runs last: the prompt stays open on the runner's screen.
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
request="$(grep -m1 'Handling access request to kTCCServiceCalendar' "$log")"
[ -n "$request" ] || { echo "::error::tccd logged no Calendar request ($(wc -l < "$log") lines)"; exit 1; }
echo "$request" | cut -c1-300
echo "$request" | grep -q 'Resp:{TCCDProcess: identifier=com.buildyourbutler.butler-ek' \
  || { echo "::error::macOS holds something other than butler-ek responsible for Calendar access"; exit 1; }
