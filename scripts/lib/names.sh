#!/bin/bash
# Butler names in bash, matching the website and setup_settings.valid_name: letters (accents too),
# digits, spaces, hyphens and apostrophes, at most 24 characters, with at least one letter or digit.
# Sourced by scripts/first-run.sh and by the installer's postinstall; works in bash 3.2 and the C
# locale.

valid_name() {
  local n="$1" chars leftover
  [ -n "$n" ] || return 1
  printf '%s' "$n" | LC_ALL=C grep -q '[[:cntrl:]]' && return 1
  # Characters, not bytes: drop UTF-8 continuation bytes before counting.
  chars="$(printf '%s' "$n" | LC_ALL=C tr -d '\200-\277' | wc -c | tr -d ' ')"
  [ "$chars" -le 24 ] || return 1
  # Whatever ASCII is left after the allowed characters and non-ASCII letters is punctuation.
  leftover="$(printf '%s' "$n" | LC_ALL=C tr -d "A-Za-z0-9 '-" | LC_ALL=C tr -d '\200-\377')"
  [ -z "$leftover" ] || return 1
  [ -n "$(printf '%s' "$n" | LC_ALL=C tr -d " '-")" ]
}

# The butler's name from the downloaded package's filename, or nothing. The website serves the
# package as "<Name>.pkg". Browsers add " (1)" (Chrome, Firefox) or "-1" (Safari) to repeats.
# "Butler.pkg" and "Butler-<anything>.pkg" are the generic download: no name.
name_from_pkg() {
  local base
  base="$(basename "$1")"
  base="${base%.pkg}"
  base="$(printf '%s' "$base" | sed -E 's/ \([0-9]+\)$//; s/-[0-9]+$//')"
  base="${base//%20/ }"
  case "$base" in Butler|Butler-*) return 0 ;; esac
  if valid_name "$base"; then printf '%s\n' "$base"; fi
}
