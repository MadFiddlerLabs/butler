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
#
# The Butler app. The package lays it in ~/Applications, which needs no password but which
# Finder's sidebar doesn't show. postinstall then moves it to /Applications when the student may
# write there without a password (an admin account), and keeps it in place on upgrades:
#
#   app_place <admin> <writable> <there> <upgrade>   system (/Applications) or home (~/Applications)
#       admin, writable, upgrade: yes or no. there: what /Applications/Butler.app is: none, ours
#       (our bundle identifier) or other (anything else, which is never touched)
#   place_app <home> <user> <upgrade>   does it, and prints one line for logs/install.log
#   app_location <home>                  where the student's Butler app is now, or nothing
#
# BUTLER_SYSTEM_APPS stands in for /Applications in tests.

BUTLER_APP_ID="com.buildyourbutler.butler-app"

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

app_bundle_id() {
  # app_bundle_id <app>: its CFBundleIdentifier, or nothing. plutil reads any plist; without it
  # (Linux test runners) an XML one is read by hand, and anything else counts as not ours.
  local plist="$1/Contents/Info.plist"
  [ -f "$plist" ] || return 0
  if command -v plutil >/dev/null 2>&1; then
    plutil -extract CFBundleIdentifier raw -o - "$plist" 2>/dev/null
  else
    tr -d '\n\t ' < "$plist" | sed -n 's|.*<key>CFBundleIdentifier</key><string>\([^<]*\)</string>.*|\1|p'
  fi
}

app_place() {
  local admin="$1" writable="$2" there="$3" upgrade="$4"
  case "$there" in
    other) echo home ;;                                   # never another app's place
    ours)
      if [ "$writable" = yes ]; then echo system; else echo home; fi ;;
    *)
      if [ "$upgrade" = yes ]; then
        echo home                                         # an upgrade keeps the app where it was
      elif [ "$admin" = yes ] && [ "$writable" = yes ]; then
        echo system
      else
        echo home
      fi ;;
  esac
}

place_app() {
  local home="$1" user="$2" upgrade="$3"
  local apps="${BUTLER_SYSTEM_APPS:-/Applications}"
  local new="$home/Applications/Butler.app" target admin=no writable=no there=none place stage old how
  target="$apps/Butler.app"
  if [ ! -d "$new" ]; then
    echo "no new Butler app in ~/Applications to place"
    return 0
  fi
  dseditgroup -o checkmember -m "$user" admin >/dev/null 2>&1 && admin=yes
  if [ "$(id -u)" = 0 ]; then
    sudo -u "$user" test -w "$apps" 2>/dev/null && writable=yes
  else
    [ -d "$apps" ] && [ -w "$apps" ] && writable=yes
  fi
  if [ -L "$target" ]; then
    there=other
  elif [ -e "$target" ]; then
    if [ "$(app_bundle_id "$target")" = "$BUTLER_APP_ID" ]; then there=ours; else there=other; fi
  fi
  place="$(app_place "$admin" "$writable" "$there" "$upgrade")"
  if [ "$place" = home ]; then
    case "$there/$upgrade/$admin" in
      other/*) echo "the Butler app stays in ~/Applications: $target is another app, left alone" ;;
      ours/*) echo "the Butler app stays in ~/Applications: $apps isn't writable, so the older copy there stays as it was" ;;
      none/yes/*) echo "the Butler app stays in ~/Applications, where it was" ;;
      none/no/no) echo "the Butler app stays in ~/Applications: $user isn't an admin" ;;
      *) echo "the Butler app stays in ~/Applications: $apps isn't writable for $user" ;;
    esac
    return 0
  fi
  # Copied next to the old one first, so a copy that fails leaves the old app working. ditto keeps
  # the signature, and codesign checks it before anything is swapped.
  stage="$apps/.Butler-installing.app"
  old="$apps/.Butler-old.app"
  rm -rf "$stage" "$old" 2>/dev/null
  if ! ditto "$new" "$stage" 2>/dev/null || ! codesign --verify --strict "$stage" >/dev/null 2>&1; then
    rm -rf "$stage" 2>/dev/null
    echo "the Butler app stays in ~/Applications: copying it to $apps failed"
    return 0
  fi
  how="moved"
  if [ "$there" = ours ]; then
    how="replaced the older one"
    if ! mv "$target" "$old" 2>/dev/null; then
      rm -rf "$stage" 2>/dev/null
      echo "the Butler app stays in ~/Applications: the older one in $apps couldn't be replaced"
      return 0
    fi
  fi
  if ! mv "$stage" "$target" 2>/dev/null; then
    [ -d "$old" ] && mv "$old" "$target" 2>/dev/null
    rm -rf "$stage" 2>/dev/null
    echo "the Butler app stays in ~/Applications: it couldn't be put in $apps"
    return 0
  fi
  rm -rf "$old" 2>/dev/null
  [ "$(id -u)" = 0 ] && chown -R "$user" "$target"
  rm -rf "$new"
  rmdir "$home/Applications" 2>/dev/null     # only when nothing else is in it
  echo "the Butler app is in $apps ($how, from ~/Applications)"
}

app_location() {
  # app_location <home>: the student's Butler app, /Applications first, or nothing.
  local apps="${BUTLER_SYSTEM_APPS:-/Applications}"
  if [ "$(app_bundle_id "$apps/Butler.app")" = "$BUTLER_APP_ID" ]; then
    echo "$apps/Butler.app"
  elif [ -d "$1/Applications/Butler.app" ]; then
    echo "$1/Applications/Butler.app"
  fi
}
