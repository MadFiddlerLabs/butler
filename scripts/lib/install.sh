#!/bin/bash
# What the installer finds in a Butler folder, and which Terminal script it opens for it. Sourced
# by the package's postinstall (packaging/macos/scripts/postinstall); works in bash 3.2.
#
#   install_state <dir>   fresh: nothing of the household's yet
#                         started: the first run began (data/first-run-started), or settings exist
#                         finished: the first run ended (data/first-run-done), or, for a Butler
#                                   from before that marker, settings and a first brief exist
#   install_opens <state> the .command in scripts/ to open in Terminal
#   add_to_path <dir> <profile>   lets `butler` work in Terminal (also used by first-run.sh)

install_state() {
  local dir="$1"
  if [ -f "$dir/data/first-run-done" ]; then
    echo finished
  elif [ -f "$dir/config/settings.yaml" ] && ls "$dir"/out/preview/brief-*.html >/dev/null 2>&1; then
    echo finished
  elif [ -f "$dir/data/first-run-started" ] || [ -f "$dir/config/settings.yaml" ]; then
    echo started
  else
    echo fresh
  fi
}

add_to_path() {
  # add_to_path <Butler folder> <profile>: one line in ~/.zprofile, so `butler` works in any new
  # Terminal window. Returns 1 when it was there already. The installer adds it so the Done
  # screen's "open Terminal and type butler" works even if the first run never opened.
  local line="export PATH=\"$1/bin:\$PATH\""
  grep -qsF "$line" "$2" && return 1
  printf '\n# Added by Butler: lets you type `butler` in Terminal.\n%s\n' "$line" >> "$2"
}

install_opens() {
  case "$1" in
    fresh) echo first-run.command ;;
    *) echo welcome-back.command ;;      # resume where you left off, or start fresh
  esac
}
