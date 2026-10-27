#!/bin/bash
# The guided first run, in Terminal. The downloaded package opens it right after installing, and
# `butler setup` runs it again any time. Plain bash, so it starts before Python exists:
#
#   1. a welcome from the butler, by the name chosen on the website (~/Butler/.butler-name)
#   2. the student's email, signed in by magic link, while Python installs in the background
#   3. the first pasted lessons: butler town, butler adults, butler brief (the first brief opens)
#   4. butler claude: the rest of the course happens in Claude Code
#
# At the end it says where the Butler app is (the way back, `butler resume`), and that it can go
# in the Dock. It never changes the Dock itself.
#
# Run again, it picks up where it stopped: a valid sign-in (Keychain `butler-account`) skips the
# name and the email, and a lesson already done is skipped. data/first-run-started marks that it
# began, data/first-run-done that it's over; the installer reads both.
#
#   first-run.sh --again   installed over an existing Butler: resume (the default) or start
#                          fresh, which moves the household's files to backups/ and deletes nothing
#
# It works offline: the account only keeps the student's place in the course. Every prompt says
# what to type, and with a screen reader (scripts/lib/plain.sh) the text is words only.
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
again=""
[ "${1:-}" = "--again" ] && again=1
mkdir -p "$home/logs" "$home/data" "$home/out"
log="$home/logs/setup.log"
resuming=""
fresh=""
[ -f "$home/data/first-run-started" ] && resuming=1
# shellcheck source-path=SCRIPTDIR source=lib/names.sh
. "$home/scripts/lib/names.sh"
# shellcheck source-path=SCRIPTDIR source=lib/heard.sh
. "$home/scripts/lib/heard.sh"
# shellcheck source-path=SCRIPTDIR source=lib/keychain.sh
. "$home/scripts/lib/keychain.sh"
# shellcheck source-path=SCRIPTDIR source=lib/plain.sh
. "$home/scripts/lib/plain.sh"
# shellcheck source-path=SCRIPTDIR source=lib/install.sh
. "$home/scripts/lib/install.sh"
plain=""
butler_plain && plain=1

# The `butler` command, from any new Terminal window. First, before anything can stop the setup,
# because every way out says "type: butler setup". The installer may have added it already.
path_added=""
add_to_path "$home" "$HOME/.zprofile" && path_added=1

bold=""; dim=""; green=""; reset=""
dot=" · "                                       # "Step 1 of 4 · Your email"
[ -n "$plain" ] && dot=": "                     # VoiceOver skips a middle dot or names it
if [ -t 1 ] && [ -z "$plain" ]; then
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
  # A tick for the eye. The sentence says it worked, so plain output leaves the tick out.
  if [ -n "$plain" ]; then say "$@"; return; fi
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
  # The body (it can hold the poll secret) goes in on stdin, and the token through a pipe that
  # curl reads as a header file, so neither shows in the process list.
  if [ -n "${3:-}" ]; then
    printf '%s' "$2" | curl -sS --max-time 10 -H 'Content-Type: application/json' \
      -H @<(printf 'Authorization: Bearer %s\n' "$3") \
      --data-binary @- -w '\n%{http_code}' "$api$1" 2>>"$log"
  else
    printf '%s' "$2" | curl -sS --max-time 10 -H 'Content-Type: application/json' \
      --data-binary @- -w '\n%{http_code}' "$api$1" 2>>"$log"
  fi
}
me() {
  # me <token>: GET /me. The token goes through a header file, never on the command line.
  curl -sS --max-time 10 -H @<(printf 'Authorization: Bearer %s\n' "$1") \
    -w '\n%{http_code}' "$api/me" 2>>"$log"
}
trim() { printf '%s' "$1" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'; }
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

start_fresh() {
  # Moves the household's files to backups/start-fresh-<date>/, after a clear yes. Deletes
  # nothing. The Terms and the "where did you hear" answer stay: they're the student's, not the
  # household's. So do Keychain and the logs. Returns 1 when the student changes their mind.
  local items=() f stamp backup confirm
  shopt -s nullglob
  for f in "$home"/config/*.yaml "$home"/data/* "$home"/out "$home"/projects \
           "$home"/budget/budget.xlsx "$home"/budget/history; do
    case "$f" in
      *.example.yaml|*/data/terms.json|*/data/heard.json) continue ;;
    esac
    [ -e "$f" ] && items+=("${f#"$home"/}")
  done
  shopt -u nullglob
  if [ "${#items[@]}" -eq 0 ]; then
    say "There's nothing of yours to move yet, so this is already a fresh start."
    return 0
  fi
  stamp="$(date +%Y-%m-%d-%H%M%S)"
  backup="backups/start-fresh-$stamp"
  say "Starting fresh moves your settings and your butler's records into a backup folder, $backup, inside your Butler folder. Nothing is deleted. Your passwords stay in Keychain."
  printf 'Type yes to start fresh, or press Enter to resume instead › '
  read -r confirm || confirm=""
  case "$(lower "$(trim "$confirm")")" in
    y|yes) ;;
    *) say "Alright, nothing moved. Let's pick up where you left off."; return 1 ;;
  esac
  for f in "${items[@]}"; do
    mkdir -p "$home/$backup/$(dirname "$f")"
    mv "$home/$f" "$home/$backup/$f"
  done
  printf '%s start fresh: moved %s to %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${items[*]}" "$backup" >> "$log"
  ok "Done. Your old settings are safe in $backup. Let's begin again."
  return 0
}

# ---------- 0. welcome back, when installed over an existing Butler ----------

if [ -n "$again" ]; then
  printf '%s welcome back\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$log"
  [ -n "$plain" ] || clear 2>/dev/null || true
  heading "Welcome back."
  say "Butler was already on this Mac. The new version is in place, and nothing of yours has changed."
  say "1. Resume where you left off."
  say "2. Start fresh. Your old settings go into a backup folder first, and nothing is deleted."
  while :; do
    printf 'Press Enter to resume, or type 2 to start fresh › '
    read -r choice || choice=""
    case "$(lower "$(trim "$choice")")" in
      ""|1|resume) break ;;
      2|fresh|"start fresh")
        start_fresh && { fresh=1; resuming=""; }
        break ;;
      *) say "Type 1 or 2, or press Enter to resume." ;;
    esac
  done
  if [ -z "$fresh" ] && [ -f "$home/data/first-run-done" ] && [ -x "$home/.venv/bin/python" ]; then
    "$butler"        # where you are in the course, the one thing to do next, the commands
    exit 0
  fi
fi
[ -f "$home/data/first-run-started" ] || date -u +%Y-%m-%dT%H:%M:%SZ > "$home/data/first-run-started"

# Signed in already (a run that stopped, or a reinstall): no need to ask the name or email again.
# The server says whether the session still works; offline, a stored one counts.
token=""
signed_in=""
signed_email=""
token="$(security find-generic-password -s butler-account -w 2>/dev/null || true)"
if [ -n "$token" ]; then
  response="$(me "$token")"
  case "$(printf '%s' "$response" | tail -n 1)" in
    200) signed_in=1; signed_email="$(json_value email "$(printf '%s' "$response" | sed '$d')")" ;;
    401|403) token="" ;;                       # the session ended: sign in again
    *) signed_in=1 ;;                          # offline, or our side: keep it
  esac
fi

# ---------- 1. welcome ----------

name="$(tr -d '\n' < "$home/.butler-name" 2>/dev/null || true)"
valid_name "$name" && [ "$name" != "Butler" ] || name=""
[ -n "$plain" ] || [ -n "$again" ] || clear 2>/dev/null || true   # a screen reader may want what came before
if [ -n "$name" ] && [ -n "$signed_in" ]; then
  heading "Hello again! It's $name, your butler."
elif [ -n "$name" ]; then
  heading "Hello! I'm $name, your new butler."
  say "Is that the name you'd like? Press Enter to keep it, or type a new one."
  printf 'Name › '
  read -r answer || answer=""
  if [ -n "$answer" ]; then
    if valid_name "$answer"; then name="$answer"; else say "I'll keep $name for now. You can change it later, for example: butler name \"Jeeves\""; fi
  fi
else
  heading "Hello! I'm your new butler."
  while [ -z "$name" ]; do
    say "What would you like to call me? Type a name: letters, numbers, spaces or hyphens, up to 24 characters."
    printf 'Name › '
    read -r answer || answer="Butler"
    if valid_name "$answer"; then name="$answer"; else say "That one won't work. Try another."; fi
  done
fi
printf '%s\n' "$name" > "$home/.butler-name"
say
if [ -n "$resuming" ]; then
  say "Let's pick up where we left off. I'll skip whatever is done already."
else
  say "Let's get me settled in. It takes about ten minutes, two of them yours."
fi

# Python installs while the student checks their email.
bootstrap_cmd="${BUTLER_BOOTSTRAP:-$home/scripts/bootstrap.sh}"
( cd "$home" && bash -c "$bootstrap_cmd" ) >>"$log" 2>&1 &
bootstrap_pid=$!

# ---------- 2. email ----------

heading "Step 1 of 4${dot}Your email"

# The Terms, once per version: the website downloads at once, so they're agreed to here.
# data/terms.json records it, and goes to the server with the sign-in (now or, offline, later).
terms_version="$(tr -d '\n' < "$home/TERMS_VERSION" 2>/dev/null || echo unknown)"
terms_file="$home/data/terms.json"
if [ "$(json_value terms_version "$(cat "$terms_file" 2>/dev/null)")" != "$terms_version" ]; then
  say "By continuing, you agree to the Terms (https://buildyourbutler.com/terms/) and Privacy (https://buildyourbutler.com/privacy/)."
  printf 'Press Enter to agree, or type no to stop › '
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

sign_in() {
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
  next_email=""
  while :; do
    if [ -n "$next_email" ]; then
      email="$next_email"
      next_email=""
    else
      printf 'Your email › '
      read -r email || email=""
    fi
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
    # The address back, so a typo shows; while it waits, a corrected one sends a new link.
    ok "Sent to $email! Check your inbox for an email from Butler and click the link inside."
    say "Waiting for your click. I'm getting my things ready meanwhile. Wrong address? Type the right one. To do this later, type skip and press Enter."
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
      if read -r -t "$poll_seconds" line; then
        line="$(printf '%s' "$line" | tr -d '[:space:]')"
        [ "$line" = "skip" ] && break
        if printf '%s' "$line" | grep -Eq '^[^@"\\]+@[^@"\\]+\.[^@"\\]+$'; then
          say "Sending a new link to $line."
          next_email="$line"
          break
        fi
        [ -n "$line" ] && say "Type your email again if it was wrong, or type skip to do this later."
      fi
    done
    [ -n "$next_email" ] && continue
    if [ -n "$token" ]; then
      printf '%s' "$token" | keychain_store butler-account 2>>"$log" \
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
}

if [ -n "$signed_in" ]; then
  ok "Already done: you're signed in${signed_email:+ as $signed_email}."
else
  sign_in
fi

# ---------- Python ----------

# One line while it works, one when it's done: a ticker of dots is noise in VoiceOver.
if kill -0 "$bootstrap_pid" 2>/dev/null; then
  say "Getting $name ready. This can take a few minutes."
fi
if ! wait "$bootstrap_pid" || [ ! -x "$home/.venv/bin/python" ]; then
  say "Something went wrong getting ready. Check your internet connection, then open a new Terminal window and type: butler setup"
  say "If it keeps happening, we can help: https://buildyourbutler.com/support/"
  exit 1
fi
ok "$name is ready."

if [ -n "$path_added" ]; then
  say "${dim}I added one line to ~/.zprofile so you can type \"butler\" in any new Terminal window.${reset}"
fi
"$butler" name "$name" >>"$log" 2>&1 || true

# ---------- 3. the first lessons ----------

# run_line <command> <line>: runs a pasted `butler <command> …` line. Returns 0 when it worked,
# 1 when it didn't, 2 when the line isn't that command. Splits like a shell would (quotes
# included) but never runs anything but `butler <command>`.
run_line() {
  local want="$1" line="$2" parts=() part
  while IFS= read -r part; do parts+=("$part"); done < <(printf '%s\n' "$line" | xargs -n 1 printf '%s\n' 2>/dev/null)
  if [ "${#parts[@]}" -lt 2 ] || [ "${parts[0]}" != "butler" ] || [ "${parts[1]}" != "$want" ]; then
    return 2
  fi
  "$butler" "${parts[@]:1}" && return 0
  return 1
}

# lesson <command> <example line> [optional]: reads a pasted `butler <command> …` line and runs it.
lesson() {
  local want="$1" example="$2" optional="${3:-}" line
  lesson_line "$example"
  while :; do
    printf 'Paste here › '
    read -r line || return 1
    line="$(trim "$line")"
    if [ -n "$optional" ] && { [ -z "$line" ] || [ "$line" = "later" ]; }; then
      return 1
    fi
    run_line "$want" "$line"
    case $? in
      0) return 0 ;;
      2) say "Paste the lesson's line, starting with: butler $want" ;;
      *) say "Let's try that again." ;;
    esac
  done
}

# The town, then a check: two towns can share a name, and the lesson says to paste it again.
town_lesson() {
  local line
  lesson town 'butler town "Lisbon"' || return 1
  while :; do
    printf 'Is that right? Press Enter, or paste the line again › '
    read -r line || return 0
    line="$(trim "$line")"
    [ -z "$line" ] && return 0
    run_line town "$line"
    [ $? -eq 2 ] && say "To change the town, paste a line starting with: butler town. If it's right, press Enter."
  done
}

# Lessons done already, by an earlier run or by hand: town, adults, brief.
done_steps="$(cd "$home" && PYTHONPATH="$home/src" "$home/.venv/bin/python" -m butler.setup_settings "done" 2>>"$log")"
is_done() { case " $done_steps " in *" $1 "*) return 0 ;; esac; return 1; }

heading "Step 2 of 4${dot}Where you live"
if is_done town; then
  ok "Already done: I know where you live."
else
  say "Every lesson is one line you copy and paste. Here's your first. Paste it at the › prompt, change the town to yours, and press Enter."
  town_lesson || exit 1
fi

heading "Step 3 of 4${dot}Who I work for"
if is_done adults; then
  ok "Already done: I know who I work for."
else
  say "Now the adults my emails are for. First names only."
  lesson adults 'butler adults Sam Alex' || exit 1
fi

heading "Step 4 of 4${dot}Your first brief"
if is_done brief; then
  ok "Already done: your first brief is written."
else
  say "Now the fun part. Paste this, and I'll write you a brief right now."
  lesson brief 'butler brief' || exit 1
fi
# The first run is over. The installer reads this: installing again offers resume or start fresh.
date -u +%Y-%m-%dT%H:%M:%SZ > "$home/data/first-run-done"

# The Butler app is the way back. Say where it is, once. The Dock stays the student's to change.
app="$(app_location "$HOME")"
case "$app" in
  "") ;;
  "$HOME"/*) say; say "To come back any time, open the Butler app: it's in the Applications folder inside your home folder, and Spotlight finds it too. Drag it to the Dock to keep it one click away." ;;
  *) say; say "To come back any time, open the Butler app in your Applications folder. Drag it to the Dock to keep it one click away." ;;
esac

# ---------- 4. into Claude Code ----------

heading "What's next"
say "That's your butler, running on your Mac. Next, I move into Claude Code, an AI helper that teaches me your calendar, your email and more, one lesson at a time. Paste this when you're ready, or press Enter to stop here for today."
if lesson claude 'butler claude' optional; then
  exit 0
fi
say
if [ -n "$app" ]; then
  say "See you soon. When you're ready, open the Butler app, or type butler resume in a new Terminal window."
else
  say "See you soon. When you're ready, type butler resume in a new Terminal window."
fi
