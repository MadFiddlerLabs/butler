#!/bin/bash
# Butler on a student's own Mac account, as most students have it: a standard account (not an
# admin), here with a home folder whose path has a space ("/Users/byb test"). Every other macOS
# test runs as the runner's account, which is an admin; this one makes a second account and runs
# everything Butler does as that account. The runner's account only creates it, starts its
# Terminal window through sudo, and looks at the result. Nothing falls back to the runner: what
# can't run as the student is listed below, and the step says so when it runs.
#
#   1. The package goes into the student's home as Installer puts it there: the payload copied by
#      the student's own account (a home-folder install's per-user installd runs as that account),
#      then the package's postinstall as the student, the way Installer.app runs it. Everything is
#      the student's; the Butler app stays in ~/Applications, since the student isn't an admin;
#      and with no screen session of theirs, no Terminal opens anywhere.
#   2. The first run in a real pseudo-terminal, typed as the student types it: the welcome, the
#      name, the Terms, the sign-in (skipped), the three lessons, then `butler claude` for real:
#      Anthropic's installer, `claude` found in the same window, Claude Code's first screen
#      (tests/e2e/student_first_run.py).
#   3. The daily schedule: the real launchd/install.sh, run as the student, writes the real jobs;
#      they load as the student; the tick runs (at load, then kickstarted) and exits cleanly, with
#      fresh lines in logs/butler.log.
#   4. An update the way a household gets one: the tick's own daily check, as the student, inside
#      the launchd job, finds v0.0.2 in a local manifest and starts it. No `butler update`.
#      Installer can't run for the student here (below), so this checks the update fails safely:
#      the old version stays, its checks pass, the tick exits cleanly and nobody is told to
#      reinstall. update.sh runs a tick-started update to the end with the real Installer.
#
# What it can't do on a hosted runner, and why:
#   - Run Installer (`installer -target CurrentUserHomeDirectory`) as the student. A home-folder
#     install goes through a per-user installd that runs only in a screen session: from the
#     student's background session Installer can't reach one ("Couldn't instantiate install
#     client"), and through sudo it reaches the runner's, which may not write in the student's
#     home ("Permission denied"). A second admin account fails the same way, so it's the missing
#     session, not admin rights. Step 1 tries it first, and fails if it ever starts working.
#   - Open Terminal for the student. Only the runner's account has a screen session (it's logged
#     in at the console), so the student has no GUI launchd domain: `launchctl asuser` fails with
#     "Domain does not support specified action". The postinstall that opens Terminal is tested
#     as the console user in installer-opens.sh.
#   - Load the jobs into the student's GUI domain (gui/<uid>), which exists only once they log in
#     at the screen. launchd/install.sh runs unchanged as the student and writes the real plists;
#     a stand-in records its two gui/<uid> calls. The same plists then load into the student's own
#     background domain (user/<uid>), with one added line, LimitLoadToSessionType Background,
#     since launchd loads an agent there only if it says so. Same account, files and commands.
#     tests/e2e/schedule-gui.sh loads the unchanged plists into the runner's GUI domain.
#   - Keychain: the student has never logged in, so they have no login keychain. Nothing here
#     stores a secret; reads find none, as on a new Mac.
#
#   tests/e2e/standard-user.sh <v0.0.1 package> <v0.0.2 package>
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
student="bybtest"
home="/Users/byb test"
b="$home/Butler"
shared="/Users/Shared/byb-e2e"          # what the student may read: the packages and the manifest
fail() {
  echo "::error::standard user: $*"
  echo "FAIL: $*" >&2
  for f in logs/install.log logs/setup.log logs/butler.log logs/launchd.err.log logs/launchd.out.log; do
    echo "--- $f"; sudo tail -n 40 "$b/$f" 2>&1 || true
  done
  exit 1
}
step() { echo; echo "== $*"; }
within() {  # within <seconds> <command…>: true once the command succeeds
  local n="$1"; shift
  for _ in $(seq 1 "$n"); do "$@" && return 0; sleep 1; done
  return 1
}
as_student() { sudo -u "$student" -H "$@"; }
# A command in the student's login shell, in the Butler folder, as Terminal would run it.
in_butler() { sudo -u "$student" -i bash -c "cd ~/Butler && $1"; }
read_file() { sudo cat "$b/$1"; }
lines() { { sudo cat "$b/logs/butler.log" 2>/dev/null || true; } | wc -l | tr -d " "; }
set_updates() {  # set_updates off|auto: the household's `updates` setting, as the student
  in_butler ".venv/bin/python -c 'import pathlib, yaml; p = pathlib.Path(\"config/settings.yaml\"); s = yaml.safe_load(p.read_text()); s[\"updates\"] = \"$1\"; p.write_text(yaml.safe_dump(s, sort_keys=False, allow_unicode=True))'"
}

step "a standard account, not an admin, with a space in its home folder's path"
password="$(openssl rand -base64 24)"   # never printed; nobody logs in with it
sudo sysadminctl -addUser "$student" -fullName "BYB Test" -password "$password" -home "$home" >/dev/null 2>&1 \
  || fail "sysadminctl couldn't add the account"
unset password
[ -d "$home" ] || sudo createhomedir -c -u "$student" >/dev/null
uid="$(id -u "$student")"
[ "$(dscl . -read "/Users/$student" NFSHomeDirectory | sed -e "s/^NFSHomeDirectory://" -e "s/^ *//" | tail -n 1)" = "$home" ] || fail "the home folder isn't $home"
[ "$(sudo -u "$student" -i printenv HOME)" = "$home" ] || fail "the student's login shell has another HOME"
if dseditgroup -o checkmember -m "$student" admin >/dev/null 2>&1; then fail "$student is an admin"; fi
echo "$student: uid $uid, home \"$home\", standard (not an admin)"

sudo mkdir -p "$shared/releases/v0.0.1"
sudo cp "$1" "$shared/Bento (1).pkg"     # as the browser saves a second download
sudo cp "$2" "$shared/Butler-v0.0.2.pkg"
sudo cp "$1" "$shared/releases/v0.0.1/Butler.pkg"
sudo chmod -R a+rX "$shared"
work="$(mktemp -d)"                      # the runner's own scratch

step "the package goes into the student's home, without admin rights, and its postinstall runs as the student"
if as_student installer -pkg "$shared/Bento (1).pkg" -target CurrentUserHomeDirectory >/dev/null 2>&1; then
  fail "Installer now works for an account with no screen session: install with it here, and finish the update below with it (see the top)"
fi
echo "::notice::Installer can't run for an account with no screen session on a hosted runner, so the student's own account copies the payload (see standard-user.sh)"
sudo pkgutil --expand-full "$shared/Bento (1).pkg" "$shared/expanded"
sudo chmod -R a+rX "$shared/expanded"
as_student ditto "$shared/expanded/Butler-component.pkg/Payload" "$home" || fail "the payload didn't copy as the student"
# Installer.app's last act, as the student (COMMAND_LINE_INSTALL unset). With no screen session
# of theirs, no Terminal can open for them, and none may open in anyone else's; the install
# still succeeds.
(cd "$shared/expanded/Butler-component.pkg/Scripts" && sudo -u "$student" -H env -u COMMAND_LINE_INSTALL ./postinstall "$shared/Bento (1).pkg" "$home") \
  || fail "postinstall failed for the student"
sudo tail -n 2 "$b/logs/install.log"
sudo tail -n 1 "$b/logs/install.log" | grep -qF "installed (fresh): could not open Terminal with first-run.command (launchctl asuser $uid open; $student isn't the one at the screen, so not open" \
  || fail "postinstall didn't say it couldn't open Terminal for the student"
sleep 3
if pgrep -x Terminal >/dev/null; then fail "Terminal opened in the runner's session for the student's install"; fi
sudo test -x "$b/bin/butler" || fail "no $b/bin/butler"
[ "$(read_file .butler-name)" = "Bento" ] || fail "the butler isn't named from the package"
[ "$(read_file .butler-version)" = "v0.0.1" ] || fail "not v0.0.1"
[ -z "$(sudo find "$b" "$home/Applications" ! -user "$student" | head -n 3)" ] \
  || fail "files in the Butler folder aren't the student's: $(sudo find "$b" ! -user "$student" | head -n 3)"
sudo test -x "$home/Applications/Butler.app/Contents/MacOS/Butler" || fail "the Butler app isn't in ~/Applications"
[ ! -e /Applications/Butler.app ] || fail "a standard account's install put the Butler app in /Applications"
sudo grep -q "the Butler app stays in ~/Applications: $student isn't an admin" "$b/logs/install.log" \
  || fail "install.log doesn't say why the app stays in ~/Applications"
sudo grep -qF "export PATH=\"$b/bin:\$PATH\"" "$home/.zprofile" || fail "butler isn't on the student's PATH"
[ "$(sudo stat -f %Su "$home/.zprofile")" = "$student" ] || fail "the student's .zprofile isn't theirs"

step "the first run, as the student types it, into Claude Code"
python3 "$here/student_first_run.py" "$student" || fail "the first run as the student"
sudo test -f "$b/data/first-run-done" || fail "the first run isn't marked done"
sudo grep -q '"terms_version"' "$b/data/terms.json" || fail "the Terms weren't recorded"
sudo grep -q "Lisbon" "$b/config/settings.yaml" || fail "the town isn't in the settings"
sudo find "$b/out/preview" -name "brief-*.html" | grep -q . || fail "no first brief"
sudo test -f "$b/data/claude-welcomed" || fail "Claude Code wasn't opened"
sudo test -x "$home/.local/bin/claude" || fail "Claude Code isn't in the student's ~/.local/bin"
in_butler 'grep -q "^claude_mode: ask" config/settings.yaml' || fail "the household's Claude mode isn't saved"
[ -z "$(sudo find "$b" "$home/.local" ! -user "$student" | head -n 3)" ] || fail "files the first run made aren't the student's"
in_butler 'bin/butler status' | tee "$work/status.txt"
grep -q "Done: *Claude Code" "$work/status.txt" || fail "butler status doesn't count Claude Code"

step "the daily schedule: the real jobs, written and loaded as the student, in the student's own launchd domain"
# The first ticks must not reach the real update list (they would install a real release over
# this test build): updates are off until the update step below.
set_updates off
# launchd/install.sh, unchanged, as the student. It loads each job into gui/<uid>, which the
# student doesn't have without a screen session: a stand-in `launchctl` records those calls
# (bootout, bootstrap) and passes anything else to the real one.
sudo mkdir -p "$shared/shim"
printf '#!/bin/bash\nif [ "${2:-}" = "gui/%s" ]; then echo "$*" >> "%s/launchctl-gui.txt"; exit 0; fi\nexec /bin/launchctl "$@"\n' \
  "$uid" "$shared" | sudo tee "$shared/shim/launchctl" >/dev/null
sudo touch "$shared/launchctl-gui.txt"
sudo chmod 666 "$shared/launchctl-gui.txt"
sudo chmod 755 "$shared/shim" "$shared/shim/launchctl"
in_butler "PATH=\"$shared/shim:\$PATH\" launchd/install.sh" || fail "launchd/install.sh failed as the student"
labels="com.buildyourbutler.butler com.buildyourbutler.morning com.buildyourbutler.portal"
before="$(lines)"
for label in $labels; do
  plist="$home/Library/LaunchAgents/$label.plist"
  grep -qxF "bootstrap gui/$uid $plist" "$shared/launchctl-gui.txt" || fail "install.sh didn't load $label for the student"
  [ "$(sudo stat -f %Su "$plist")" = "$student" ] || fail "$label.plist isn't the student's"
  sudo plutil -lint "$plist" >/dev/null || fail "$label.plist isn't a valid plist"
  sudo grep -qF "<string>$b/run.sh</string>" "$plist" || fail "$label.plist doesn't run $b/run.sh"
  # The same plist, into the student's own background domain. launchd loads an agent only into
  # the session types it names, Aqua (the screen) when it names none, so this one line says
  # Background. Nothing else in the plist changes.
  as_student plutil -insert LimitLoadToSessionType -string Background "$plist"
  as_student launchctl bootstrap "user/$uid" "$plist" || fail "$label didn't load in user/$uid"
  as_student launchctl print "user/$uid/$label" >/dev/null || fail "$label isn't loaded"
done
job() { as_student launchctl print "user/$uid/com.buildyourbutler.butler"; }
ran() {  # ran <n>: the tick has run at least n times and isn't running now
  local out runs; out="$(job)"
  runs="$(printf '%s\n' "$out" | sed -n 's/^[[:space:]]*runs = //p' | head -n 1)"
  [ "${runs:-0}" -ge "$1" ] && [ "$(printf "%s\n" "$out" | grep -m 1 -E "^[[:space:]]*state = ")" = "$(printf "\tstate = not running")" ] \
    && printf "%s\n" "$out" | grep -Eq "last exit code = [0-9]+"
}
exit_code() { job | sed -n 's/^[[:space:]]*last exit code = //p' | head -n 1; }
within 300 ran 1 || { job || true; fail "the tick didn't run at load"; }
[ "$(exit_code)" = "0" ] || fail "the tick at load exited with $(exit_code)"
[ "$(lines)" -gt "$before" ] || fail "the tick at load wrote nothing to logs/butler.log"
sudo tail -n +"$((before + 1))" "$b/logs/butler.log"
before="$(lines)"
as_student launchctl kickstart -p "user/$uid/com.buildyourbutler.butler" || fail "kickstart failed"
within 300 ran 2 || { job || true; fail "the kickstarted tick didn't finish"; }
[ "$(exit_code)" = "0" ] || fail "the kickstarted tick exited with $(exit_code)"
[ "$(lines)" -gt "$before" ] || fail "the kickstarted tick wrote nothing to logs/butler.log"
sudo tail -n +"$((before + 1))" "$b/logs/butler.log"
[ -z "$(sudo find "$b/logs" "$b/data" ! -user "$student" | head -n 3)" ] || fail "the tick left files that aren't the student's"

step "an update the way a household gets one: the tick's own check, as the student"
# The real tick job, with the update list and the rollback copy pointed at local files. The
# packages are unsigned test builds: BUTLER_UPDATE_ALLOW_UNSIGNED works only for a build that
# carries its own version's marker (tests/test_updates.py). BUTLER_API: nothing listens there, so
# the outcome is never reported to the real website.
sha="$(shasum -a 256 "$shared/Butler-v0.0.2.pkg" | awk '{print $1}')"
size="$(stat -f %z "$shared/Butler-v0.0.2.pkg")"
printf '{"available":true,"version":"v0.0.2","pkg_url":"%s","sha256":"%s","size":%s,"notes":"Test release v0.0.2."}\n' \
  "$shared/Butler-v0.0.2.pkg" "$sha" "$size" > "$work/stable.json"
sudo cp "$work/stable.json" "$shared/stable.json"
sudo chmod a+r "$shared/stable.json"
tick_plist="$home/Library/LaunchAgents/com.buildyourbutler.butler.plist"
as_student launchctl bootout "user/$uid/com.buildyourbutler.butler" || fail "couldn't stop the tick"
as_student plutil -insert EnvironmentVariables -json "{
  \"BUTLER_UPDATE_MANIFEST\": \"$shared/stable.json\", \"BUTLER_RELEASES_URL\": \"$shared/releases\",
  \"BUTLER_UPDATE_ALLOW_UNSIGNED\": \"1\", \"BUTLER_API\": \"http://127.0.0.1:9/api\"}" "$tick_plist" \
  || fail "couldn't point the tick at the test update list"
set_updates auto
before="$(lines)"
as_student launchctl bootstrap "user/$uid" "$tick_plist" || fail "couldn't load the tick again"
# RunAtLoad: the tick runs at once. Its update check is due (it has never checked), finds v0.0.2
# and starts it: download, checksum, signature, the rollback copy, then Installer, which can't
# run for this account here (see the top). The way back finds v0.0.1 untouched and checks it.
within 900 ran 1 || { job || true; fail "the tick with the update didn't finish"; }
new="$(sudo tail -n +"$((before + 1))" "$b/logs/butler.log")"
printf '%s\n' "$new"
[ "$(exit_code)" = "0" ] || fail "the tick with the update exited with $(exit_code)"
grep -q "update check: v0.0.2 will install" <<<"$new" || fail "the tick's update check didn't find v0.0.2"
grep -q "update: installing v0.0.2 over v0.0.1" <<<"$new" || fail "the tick didn't start the update"
[ "$(read_file .butler-version)" = "v0.0.1" ] || fail "the version changed: $(read_file .butler-version)"
result="$(in_butler 'PYTHONPATH=src .venv/bin/python -c "import json; from butler import updates; print(json.dumps(updates.load_state()[\"last_result\"]))"')"
echo "$result"
grep -q '"status": "rolled_back"' <<<"$result" || fail "the update's result isn't recorded"
grep -q "Installer didn't finish" <<<"$result" || fail "the result doesn't say Installer didn't finish"
grep -q '"restored_ok": true' <<<"$result" || fail "the old version, untouched and working, wasn't counted as restored"
if grep -q "the way back failed\|failed rollback\|failed too" <<<"$new"; then fail "the household was told to reinstall a Butler that works"; fi
sudo test -f "$b/data/updates/Butler-v0.0.1.pkg" || fail "the rollback copy isn't kept"
in_butler 'grep -q "^name: Bento" config/settings.yaml' || fail "the update changed the butler's name"
in_butler 'grep -q "Lisbon" config/settings.yaml' || fail "the update lost the town"
[ -z "$(sudo find "$b" ! -user "$student" | head -n 3)" ] || fail "the update left files that aren't the student's: $(sudo find "$b" ! -user "$student" | head -n 3)"

step "the schedule comes out cleanly"
for label in $labels; do as_student launchctl bootout "user/$uid/$label" || fail "couldn't stop $label"; done
in_butler "PATH=\"$shared/shim:\$PATH\" launchd/uninstall.sh" || fail "launchd/uninstall.sh failed as the student"
if sudo ls "$home/Library/LaunchAgents/" | grep -q buildyourbutler; then fail "uninstall.sh left a plist behind"; fi

echo
echo "a standard account with a space in its home: all good"
