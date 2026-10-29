#!/bin/bash
# Watches a Mac persona run that as-student.sh runs in the background, for personas.yml. A hosted
# Mac went silent partway through two rehearsals (29 Oct 2026): the runner stopped answering, so
# GitHub kept no log, no artifact, and a cancel didn't land. The run now goes in the background, and
# personas.yml watches it in short steps, each followed by an upload of <folder>. Whatever happens
# to the runner, the last upload shows how far the run got and how the Mac was doing.
#
#   tests/personas/watch.sh <folder> <minutes>    watch up to <minutes>; exit 0 either way
#   tests/personas/watch.sh <folder> done         watch until the run ends; exit with its code
#
# <folder> holds run.log (as-student.sh's output) and, once it ends, exit-code. Each minute this
# prints what's new in run.log, adds a line of the Mac's health to vitals.log, and copies the
# run's own progress (live.md, Butler's logs) into <folder>/snapshot.
set -uo pipefail
folder="$1"
limit="$2"
shared="/Users/Shared/byb-persona"
home="/Users/byb test"
mkdir -p "$folder/snapshot"
offset_file="$folder/.printed"

vitals() {
  {
    echo "== $(date -u +%H:%M:%S) $(uptime | sed 's/.*load/load/')"
    memory_pressure -Q 2>/dev/null | tail -n 1
    df -h / | tail -n 1
    ps -Ao pid,user,%cpu,rss,etime,comm -r | head -n 8
  } >> "$folder/vitals.log" 2>&1
}

# shellcheck disable=SC2024  # the copies are the runner's, written as the runner: only the reads need root
snapshot() {
  sudo -n cp "$shared/out/live.md" "$folder/snapshot/" 2>/dev/null
  sudo -n cp "$shared/admin.log" "$folder/snapshot/" 2>/dev/null
  for f in install.log setup.log butler.log; do
    sudo -n tail -n 200 "$home/Butler/logs/$f" > "$folder/snapshot/$f" 2>/dev/null
  done
  sudo -n tail -n 100 "$home/persona-fakes/launchctl.log" > "$folder/snapshot/launchctl.log" 2>/dev/null
  sudo -n chown -R "$(id -un)" "$folder/snapshot" 2>/dev/null
}

print_new() {
  local printed size
  printed="$(cat "$offset_file" 2>/dev/null || echo 0)"
  size="$(wc -c < "$folder/run.log" 2>/dev/null | tr -d ' ' || echo 0)"
  if [ "${size:-0}" -gt "$printed" ]; then
    tail -c +"$((printed + 1))" "$folder/run.log"
    echo "$size" > "$offset_file"
  fi
}

start=$SECONDS
while :; do
  print_new
  if [ -f "$folder/exit-code" ]; then
    snapshot
    code="$(cat "$folder/exit-code")"
    echo "== the run ended (exit $code)"
    [ "$limit" = "done" ] && exit "$code"
    exit 0
  fi
  vitals
  snapshot
  if [ "$limit" != "done" ] && [ $((SECONDS - start)) -ge $((limit * 60)) ]; then
    echo "== still running after $limit more minutes; the Mac now:"
    tail -n 12 "$folder/vitals.log"
    exit 0
  fi
  sleep 60
done
