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
# shellcheck source-path=SCRIPTDIR source=lib/names.sh
. "$home/scripts/lib/names.sh"
# shellcheck source-path=SCRIPTDIR source=lib/heard.sh
. "$home/scripts/lib/heard.sh"

# The `butler` command, from any new Terminal window. First, before anything can stop the setup,
# because every way out says "type: butler setup".
profile="$HOME/.zprofile"
path_line="export PATH=\"$home/bin:\$PATH\""
path_added=""
if ! grep -qsF "$path_line" "$profile"; then
  printf '\n# Added by Butler: lets you type `butler` in Terminal.\n%s\n' "$path_line" >> "$profile"
  path_added=1
fi

bold=""; dim=""; green=""; reset=""
if [ -t 1 ]; then
  bold="$(printf '\033[1m')"; dim="$(printf '\033[2m')"; green="$(printf '\033[32m')"; reset="$(printf '\033[0m')"
fi
# Prose wraps to the window (at most 80 columns, a new Terminal's width), never mid-word, and a
# `butler <command>` mentioned in a sentence stays on one line so it can be copied whole.
# BUTLER_COLUMNS overrides the width (tests use it).
columns="${BUTLER_COLUMNS:-$(tput cols 2>/dev/null || echo 80)}"
case "$columns" in ''|*[!0-9]*) columns=80 ;; esac
[ "$columns" -gt 80 ] && [ -z "${BUTLER_COLUMNS:-}" ] && columns=80
wrap_width=$((columns - 4))
wrap() {
  awk '{ out = ""; s = $0
         while (match(s, /butler [a-z]/)) { out = out substr(s, 1, RSTART + 5) "\037"; s = substr(s, RSTART + 7) }
         print out s }' | fold -s -w "$1" | tr '\037' ' ' | sed 's/ *$//'
}
say() { printf '%s\n' "$*" | wrap "$wrap_width"; }
ok() {
  printf '%s\n' "$*" | wrap "$((wrap_width - 2))" | {
    IFS= read -r first; printf '%s✓%s %s\n' "$green" "$reset" "$first"
    while IFS= read -r rest; do printf '  %s\n' "$rest"; done
  }
}
heading() { printf '\n%s%s%s\n' "$bold" "$*" "$reset"; }
lesson_line() { printf '\n    %s%s%s\n\n' "$bold" "$*" "$reset"; }

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
say "Let's get me settled in. It takes about ten minutes, two of them yours."

# Python installs while the student checks their email.
bootstrap_cmd="${BUTLER_BOOTSTRAP:-$home/scripts/bootstrap.sh}"
( cd "$home" && bash -c "$bootstrap_cmd" ) >>"$log" 2>&1 &
bootstrap_pid=$!

# ---------- 2. email ----------

heading "Step 1 of 4 · Your email"

# The Terms, once per version: the website downloads at once, so they're agreed to here.
# data/terms.json records it, and goes to the server with the sign-in (now or, offline, later).
terms_version="$(tr -d '\n' < "$home/TERMS_VERSION" 2>/dev/null || echo unknown)"
terms_file="$home/data/terms.json"
if [ "$(json_value terms_version "$(cat "$terms_file" 2>/dev/null)")" != "$terms_version" ]; then
  say "By continuing, you agree to the Terms (https://buildyourbutler.com/terms/) and Privacy (https://buildyourbutler.com/privacy/)."
  printf 'Continue? [Y/n] '
  read -r agree || agree=""
  case "$(printf '%s' "$agree" | tr '[:upper:]' '[:lower:]')" in
    n|no)
      kill "$bootstrap_pid" 2>/dev/null || true
      say "No problem. Nothing has been sent anywhere. If you change your mind, open a new Terminal window and type: butler setup"
      exit 0
      ;;
  esac
  printf '{"terms_version":"%s","terms_accepted_at":"%s"}\n' "$terms_version" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$terms_file"
fi
terms_accepted_at="$(json_value terms_accepted_at "$(cat "$terms_file")")"

# Where the student heard about Butler, asked once: data/heard.json keeps the answer, or the skip.
# Only one of the ten tokens goes with the sign-in (now or, offline, later). Never free text.
heard_file="$home/data/heard.json"
if [ ! -f "$heard_file" ]; then
  say "Where did you hear about Butler? (optional)"
  i=0
  for label in "${HEARD_LABELS[@]}"; do i=$((i + 1)); printf '  %2d. %s\n' "$i" "$label"; done
  heard=""
  while :; do
    printf 'Type a number, or press Enter to skip › '
    read -r answer || answer=""
    [ -z "$(printf '%s' "$answer" | tr -d '[:space:]')" ] && break
    heard="$(heard_token "$answer")" && break
    say "Type a number from 1 to ${#HEARD_TOKENS[@]}, or press Enter to skip."
  done
  if [ -n "$heard" ]; then printf '{"heard":"%s"}\n' "$heard"; else printf '{"heard":null}\n'; fi > "$heard_file"
fi
heard="$(json_value heard "$(cat "$heard_file" 2>/dev/null)")"
heard_known "$heard" || heard=""

say "I'll email you a link to sign in. It keeps your place in the course."
say "We never see your household's data. It goes only from this Mac to services you choose, such as Anthropic and Google."
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
  response="$(post /auth/start "{\"email\":\"$email\",\"butler_name\":\"$name\",\"client\":\"setup/$version\",\"terms_version\":\"$terms_version\",\"terms_accepted_at\":\"$terms_accepted_at\"${heard:+,\"heard\":\"$heard\"}}")"
  code="$(printf '%s' "$response" | tail -n 1)"
  body="$(printf '%s' "$response" | sed '$d')"
  if [ "$code" != "200" ]; then
    if [ "$code" = "000" ] || [ -z "$code" ]; then
      say "I can't reach buildyourbutler.com right now. Let's carry on, and sign in later with: butler setup"
      email=""
      break
    fi
    # A 5xx is our side (email sign-in down or not open yet), not the student's: carry on.
    case "$code" in
      5??)
        say "Sign-in isn't available right now. That's on our side, not yours. Let's carry on, and sign in later with: butler setup"
        email=""
        break ;;
    esac
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
    say "Would you like one short email a week with your next lesson? You can stop any time."
    printf 'Weekly lesson email? Type yes, or press Enter for no › '
    read -r nudge || nudge=""
    case "$(printf '%s' "$nudge" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in
      y|yes) nudges=true ;;
      *) nudges=false ;;
    esac
    post /prefs "{\"nudges\":$nudges}" "$token" >/dev/null || true
    if [ "$nudges" = true ]; then ok "Done. One email a week, and every one has a stop link."; else say "No weekly emails. Change your mind any time on buildyourbutler.com."; fi
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
  say "Something went wrong getting ready. Check your internet connection, then open a new Terminal window and type: butler setup"
  say "If it keeps happening, email support@buildyourbutler.com and attach logs/setup.log from your Butler folder."
  exit 1
fi
ok "$name is ready."

if [ -n "$path_added" ]; then
  say "${dim}I added one line to ~/.zprofile so you can type \"butler\" in any new Terminal window.${reset}"
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
say "Every lesson is one line you copy and paste. Here's your first. Paste it below, change the town to yours, and press Enter."
lesson town 'butler town "Lisbon"' || exit 1

heading "Step 3 of 4 · Who I work for"
say "Now the adults my emails are for. First names only."
lesson adults 'butler adults Sam Alex' || exit 1

heading "Step 4 of 4 · Your first brief"
say "Now the fun part. Paste this, and I'll write you a brief right now."
lesson brief 'butler brief' || exit 1

# ---------- 4. into Claude Code ----------

heading "What's next"
say "That's your butler, running on your Mac. Next, I move into Claude Code, an AI helper that teaches me your calendar, your email and more, one lesson at a time. Paste this when you're ready, or press Enter to stop here for today."
if lesson claude 'butler claude' optional; then
  exit 0
fi
say
say "See you soon. When you're ready, open Terminal and type: butler claude"
