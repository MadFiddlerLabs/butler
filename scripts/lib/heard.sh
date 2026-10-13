#!/bin/bash
# "Where did you hear about Butler?": the ten sources the website takes as `heard` on
# /api/auth/start (docs/ACCOUNTS.md), in the order the first run lists them. Only a token from this
# list is ever sent, never the student's own words. account.HEARD_SOURCES is the same list, and
# tests/test_first_run.py checks that they match. Sourced by scripts/first-run.sh; works in bash 3.2.

HEARD_TOKENS=(friend search reddit hacker_news product_hunt youtube social newsletter podcast other)
# shellcheck disable=SC2034  # used by first-run.sh
HEARD_LABELS=("A friend or family member" "A web search" "Reddit" "Hacker News" "Product Hunt"
              "YouTube" "Social media" "A newsletter" "A podcast" "Somewhere else")

# heard_token <answer>: the token for a number from 1 to 10, or nothing (and false).
heard_token() {
  local n
  n="$(printf '%s' "$1" | tr -d '[:space:].')"
  case "$n" in [1-9]|10) ;; *) return 1 ;; esac
  printf '%s\n' "${HEARD_TOKENS[$((n - 1))]}"
}

# heard_known <token>: true for one of the ten. What data/heard.json holds is checked before sending.
heard_known() {
  local t
  for t in "${HEARD_TOKENS[@]}"; do [ "$t" = "$1" ] && return 0; done
  return 1
}
