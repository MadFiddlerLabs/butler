#!/bin/bash
# keychain_store <service>: stores the secret on stdin in the login Keychain (account "butler").
#
# The secret never goes on a command line, where any account on this Mac can read it in the
# process list. `security -i` reads its command from stdin, and the secret travels inside that
# command hex-encoded (-X), so no quote or backslash in it can break the command. The plain
# `security add-generic-password ... -w` with no value would prompt instead, but it reads the
# terminal rather than a pipe whenever one is open. Sourced by store-secret.sh and first-run.sh;
# works in bash 3.2. config.store_secret() does the same from Python.

keychain_store() {
  local service="$1" hex
  case "$service" in
    butler-[a-z]*) ;;
    *) echo "keychain_store: not a Butler service: $service" >&2; return 2 ;;
  esac
  # od reads the secret from stdin; -v keeps repeated runs of bytes (no "*" lines).
  hex="$(od -An -v -tx1 | tr -d ' \n')"
  [ -n "$hex" ] || return 1
  printf 'add-generic-password -U -s %s -a butler -X %s\n' "$service" "$hex" | security -i >/dev/null
}
