#!/bin/bash
# The guided first run, in Terminal. The downloaded package opens it right after installing, and
# `butler setup` runs it again any time. Plain bash, so it starts before Python exists:
#
#   1. a welcome from the butler, by the name chosen on the website (~/Butler/.butler-name)
#   2. the student's email, signed in by magic link, while Python installs in the background
#   3. the first pasted lessons: butler town, butler adults, butler brief (the first brief opens)
#   4. butler claude: the rest of the course happens in Claude Code
#
# It works offline: the account only keeps the student's place in the course.
# Test hooks: BUTLER_API, BUTLER_BOOTSTRAP (a command instead of scripts/bootstrap.sh),
# BUTLER_POLL_SECONDS, BUTLER_POLL_LIMIT.
set -uo pipefail
home="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
api="${BUTLER_API:-https://buildyourbutler.com/api}"
api="${api%/}"
poll_seconds="${BUTLER_POLL_SECONDS:-3}"
poll_limit="${BUTLER_POLL_LIMIT:-300}"      # 300 polls of 3 seconds: 15 minutes, the link's life
version="$(cat "$home/.butler-version" 2>/dev/null || echo dev)"
butler="$home/bin/butler"
mkdir -p "$home/logs" "$home/data" "$home/out"
log="$home/logs/setup.log"

bold=""; dim=""; green=""; reset=""
if [ -t 1 ]; then
  bold="$(printf '\033[1m')"; dim="$(printf '\033[2m')"; green="$(printf '\033[32m')"; reset="$(printf '\033[0m')"
fi
say() { printf '%s\n' "$*"; }
ok() { printf '%s✓%s %s\n' "$green" "$reset" "$*"; }
heading() { printf '\n%s%s%s\n' "$bold" "$*" "$reset"; }
lesson_line() { printf '\n    %s%s%s\n\n' "$bold" "$*" "$reset"; }

valid_name() {
  # Letters, digits, spaces, hyphens and apostrophes, up to 24 characters (the website's rule).
  local n="$1"
  [ -n "$n" ] && [ "${#n}" -le 24 ] && ! printf '%s' "$n" | LC_ALL=C grep -q "[^[:alnum:] '-]"
}
json_value() {
  # The string or number value of one key in a flat JSON object. Good enough for the account API.
  printf '%s' "$2" | tr -d '\n' | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\{0,1\}\([^\",}]*\).*/\1/p"
}
post() {
  # post <path> <json> [token]: prints the body, then the HTTP code on the last line.
  local auth=()
  [ -n "${3:-}" ] && auth=(-H "Authorization: Bearer $3")
  curl -sS --max-time 10 -H 'Content-Type: application/json' ${auth[@]+"${auth[@]}"} \
    -d "$2" -w '\n%{http_code}' "$api$1" 2>>"$log"
}

# ---------- 1. welcome ----------

name="$(tr -d '\n' < "$home/.butler-name" 2>/dev/null || true)"
valid_name "$name" && [ "$name" != "Butler" ] || name=""
clear 2>/dev/null || true
if [ -n "$name" ]; then
  heading "Hello! I'm $name, your new butler."
  say "Is that the name you'd like? Press Enter to keep it, or type a new one."
  printf '› '
  read -r answer || answer=""
  if [ -n "$answer" ]; then
    if valid_name "$answer"; then name="$answer"; else say "I'll keep $name for now. You can change it later with: butler name \"…\""; fi
  fi
else
  heading "Hello! I'm your new butler."
  while [ -z "$name" ]; do
    say "What would you like to call me? (Letters, numbers, spaces, hyphens; up to 24 characters.)"
    printf '› '
    read -r answer || answer="Butler"
    if valid_name "$answer"; then name="$answer"; else say "That one won't work. Try another."; fi
  done
fi
printf '%s\n' "$name" > "$home/.butler-name"
say
say "Let's get me settled in. It takes about five minutes, and you can't break anything."

# Python installs while the student checks their email.
bootstrap_cmd="${BUTLER_BOOTSTRAP:-$home/scripts/bootstrap.sh}"
( cd "$home" && bash -c "$bootstrap_cmd" ) >>"$log" 2>&1 &
bootstrap_pid=$!

# ---------- 2. email ----------

heading "Step 1 of 4 · Your email"
say "I'll email you a link to sign in. It keeps your place in the course. Nothing from your"
say "household ever leaves this Mac."
token=""
email=""
while :; do
  printf 'Your email › '
  read -r email || email=""
  email="$(printf '%s' "$email" | tr -d '[:space:]')"
  if [ -z "$email" ] || [ "$email" = "skip" ]; then
    say "No problem. Sign in later with: butler setup"
    email=""
    break
  fi
  if ! printf '%s' "$email" | grep -Eq '^[^@"\\]+@[^@"\\]+\.[^@"\\]+$'; then
    say "That doesn't look like an email address. Try again, or type skip."
    continue
  fi
  response="$(post /auth/start "{\"email\":\"$email\",\"butler_name\":\"$name\",\"client\":\"setup/$version\"}")"
  code="$(printf '%s' "$response" | tail -n 1)"
  body="$(printf '%s' "$response" | sed '$d')"
  if [ "$code" != "200" ]; then
    if [ "$code" = "000" ] || [ -z "$code" ]; then
      say "I can't reach buildyourbutler.com right now. Let's carry on, and sign in later with: butler setup"
      email=""
      break
    fi
    error="$(json_value error "$body")"
    say "That didn't work${error:+ ($error)}. Try again, or type skip."
    continue
  fi
  request_id="$(json_value request_id "$body")"
  poll_secret="$(json_value poll_secret "$body")"
  ok "Sent! Check your inbox for an email from Butler and click the link inside."
  say "${dim}Waiting for your click. I'm getting my things ready meanwhile. (Type skip and press Enter to do this later.)${reset}"
  polls=0
  while [ "$polls" -lt "$poll_limit" ]; do
    polls=$((polls + 1))
    response="$(post /auth/poll "{\"request_id\":\"$request_id\",\"poll_secret\":\"$poll_secret\"}")"
    body="$(printf '%s' "$response" | sed '$d')"
    status="$(json_value status "$body")"
    if [ "$status" = "verified" ]; then
      token="$(json_value session_token "$body")"
      break
    fi
    if [ "$status" = "expired" ]; then
      say "That link expired. Let's send a new one."
      break
    fi
    if read -r -t "$poll_seconds" line && [ "$line" = "skip" ]; then
      break
    fi
  done
  if [ -n "$token" ]; then
    security add-generic-password -U -s butler-account -a butler -w "$token" >>"$log" 2>&1 \
      || say "I couldn't save your sign-in to Keychain. You can sign in again later with: butler setup"
    post /progress '{"lesson":"setup.email","status":"done"}' "$token" >/dev/null || true
    ok "You're signed in as $email."
    break
  fi
  [ "${status:-}" = "expired" ] && continue
  say "We'll finish signing in later. Type: butler setup"
  break
done

# ---------- Python ----------

if kill -0 "$bootstrap_pid" 2>/dev/null; then
  printf 'Getting %s ready' "$name"
  while kill -0 "$bootstrap_pid" 2>/dev/null; do printf '.'; sleep 2; done
  printf '\n'
fi
if ! wait "$bootstrap_pid" || [ ! -x "$home/.venv/bin/python" ]; then
  say "Something went wrong getting ready. Check your internet connection, then type: butler setup"
  say "If it keeps happening, email support@buildyourbutler.com and attach logs/setup.log from your Butler folder."
  exit 1
fi
ok "$name is ready."

# The `butler` command, from any Terminal window.
profile="$HOME/.zprofile"
path_line="export PATH=\"$home/bin:\$PATH\""
if ! grep -qsF "$path_line" "$profile"; then
  printf '\n# Added by Butler: lets you type `butler` in Terminal.\n%s\n' "$path_line" >> "$profile"
  say "${dim}I added one line to ~/.zprofile so you can type \"butler\" in any Terminal window.${reset}"
fi
"$butler" name "$name" >>"$log" 2>&1 || true

# ---------- 3. the first lessons ----------

# lesson <command> <example line> [optional]: reads a pasted `butler <command> …` line and runs it.
lesson() {
  local want="$1" example="$2" optional="${3:-}" line parts
  lesson_line "$example"
  while :; do
    printf '› '
    read -r line || return 1
    line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    if [ -n "$optional" ] && { [ -z "$line" ] || [ "$line" = "later" ]; }; then
      return 1
    fi
    # Split like a shell would (quotes included) but never run anything but `butler <command>`.
    parts=()
    while IFS= read -r part; do parts+=("$part"); done < <(printf '%s\n' "$line" | xargs -n 1 printf '%s\n' 2>/dev/null)
    if [ "${#parts[@]}" -lt 1 ] || [ "${parts[0]}" != "butler" ] || [ "${parts[1]:-}" != "$want" ]; then
      say "Paste the line above, starting with: butler $want"
      continue
    fi
    if "$butler" "${parts[@]:1}"; then
      return 0
    fi
    say "Let's try that again."
  done
}

heading "Step 2 of 4 · Where you live"
say "Every lesson is one line you copy and paste. Here's your first. Paste it below, change"
say "the town to yours, and press Enter."
lesson town 'butler town "Lisbon"' || exit 1

heading "Step 3 of 4 · Who I work for"
say "Now the adults my emails are for. First names only."
lesson adults 'butler adults Sam Alex' || exit 1

heading "Step 4 of 4 · Your first brief"
say "Now the fun part. Paste this, and I'll write you a brief right now."
lesson brief 'butler brief' || exit 1

# ---------- 4. into Claude Code ----------

heading "What's next"
say "That's your butler, running on your Mac. Next, I move into Claude Code, an AI helper that"
say "teaches me your calendar, your email and more, one lesson at a time. Paste this when you're"
say "ready, or press Enter to stop here for today."
if lesson claude 'butler claude' optional; then
  exit 0
fi
say
say "See you soon. When you're ready, open Terminal and type: butler claude"
