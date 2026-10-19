#!/bin/bash
# Signed to signed: the update students will really get. Installs a published release (signed,
# notarized, from GitHub Releases: the latest, or the oldest still offered an update) into this home, adds a household's files, then lets THAT
# release's own updater install the candidate: a signed, notarized package built by
# .github/workflows/release.yml. Nothing relaxes a check here: BUTLER_UPDATE_ALLOW_UNSIGNED is
# never set, and the rollback copy comes from the real GitHub Release.
#
# Before the real update, three tampered copies of the candidate must be refused, each leaving
# the household's files and the version as they were:
#   - a download that doesn't match the manifest's checksum,
#   - a byte changed inside the package, with a manifest that matches it,
#   - the package unpacked, edited and repacked (so unsigned), with a matching manifest.
#
#   tests/e2e/signed-update.sh <candidate.pkg> <candidate-version> <published-version>
#
# CI only (release.yml, job signed-update): it installs into ~/Butler.
set -euo pipefail
candidate="$1"; new="$2"; old="$3"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
b="$HOME/Butler"
fail() { echo "FAIL: $*" >&2; exit 1; }
step() { echo; echo "== $*"; }
[ ! -e "$b" ] || fail "$b exists already; this test needs a clean home"
[ -f "$candidate" ] || fail "no candidate package at $candidate"
[ -z "${BUTLER_UPDATE_ALLOW_UNSIGNED:-}" ] || fail "BUTLER_UPDATE_ALLOW_UNSIGNED must not be set for this test"

manifest() {  # manifest <pkg> [<sha256>]: the shape the website builds (scripts/fetch-release.mjs)
  local sha size
  sha="${2:-$(shasum -a 256 "$1" | awk '{print $1}')}"; size="$(stat -f %z "$1")"
  printf '{"available":true,"version":"%s","pkg_url":"%s","sha256":"%s","size":%s,"min_version":null,"major":false,"notes":"Signed to signed test of %s."}\n' \
    "$new" "$1" "$sha" "$size" "$new" > "$work/stable.json"
}
fingerprint() {  # the household's own files, byte for byte
  (cd "$b" && shasum -a 256 config/settings.yaml config/category_map.yaml data/household_notes.md \
    projects/home.yaml budget/budget.xlsx .butler-name)
}
py() { (cd "$b" && PYTHONPATH=src .venv/bin/python -c "$1"); }
signed_by_us() {  # the same three checks the updater makes, shown in the log
  local sig
  sig="$(pkgutil --check-signature "$1")" || { echo "$sig"; fail "$1: pkgutil refused it"; }
  echo "$sig"
  grep -q "1\. Developer ID Installer: .*(VB9CHJM4AN)" <<< "$sig" || fail "$1: not our Developer ID"
  spctl --assess -vv --type install "$1"
  xcrun stapler validate "$1"
}
refused() {  # refused <what> <expected message>: the update fails and changes nothing
  "$b/bin/butler" update --yes > "$work/refused.log" 2>&1 && { cat "$work/refused.log"; fail "$1 was installed"; }
  cat "$work/refused.log"
  grep -q "$2" "$work/refused.log" || fail "$1: expected \"$2\""
  [ "$(cat "$b/.butler-version")" = "$old" ] || fail "$1 changed the version"
  fingerprint | diff - "$work/household.before" || fail "$1 changed the household's files"
}

step "the published $old and the candidate $new are both signed, notarized and stapled"
curl -fsSL -o "$work/Bento.pkg" "https://github.com/MadFiddlerLabs/butler/releases/download/$old/Butler.pkg"
signed_by_us "$work/Bento.pkg"
signed_by_us "$candidate"

step "install $old with Installer, then a household's files"
installer -pkg "$work/Bento.pkg" -target CurrentUserHomeDirectory
[ "$(cat "$b/.butler-version")" = "$old" ] || fail "$old not installed"
test ! -e "$b/.butler-unsigned-build" || fail "$old carries the unsigned-build marker"
cp "$b/config/settings.example.yaml" "$b/config/settings.yaml"
cp "$b/config/category_map.example.yaml" "$b/config/category_map.yaml"
mkdir -p "$b/data" "$b/projects"
echo "- the Johnsons' plumber is Rui (invented)" > "$b/data/household_notes.md"
cp "$b/examples/projects/home.yaml" "$b/projects/home.yaml"
cp "$b/budget/budget.example.xlsx" "$b/budget/budget.xlsx"
fingerprint > "$work/household.before"
"$b/scripts/bootstrap.sh"

# BUTLER_COLUMNS: no wrapping, so the greps below see whole sentences. BUTLER_API: nothing listens
# there, so these test updates never report an outcome to the real website (update_report.py).
export BUTLER_UPDATE_MANIFEST="$work/stable.json" BUTLER_COLUMNS=1000 BUTLER_API=http://127.0.0.1:9/api

step "what $old's updater decides about $new under updates: auto"
manifest "$candidate"
decision="$(py 'import json, os; from butler import updates, config
m = json.load(open(os.environ["BUTLER_UPDATE_MANIFEST"]))
print(updates.decide(config.load_settings(), m, updates.installed_version(), "ci").action)')"
echo "decision: $decision (install = overnight on its own; ask = waits for an \"update\" reply)"
case "$decision" in install|ask) ;; *) fail "$old's updater would not offer $new ($decision)" ;; esac

step "tampered copies of $new are refused by $old's updater"
python3 - "$candidate" "$work/flipped.pkg" <<'EOF'
import sys
data = bytearray(open(sys.argv[1], "rb").read())
data[len(data) // 2] ^= 0xFF   # one byte inside the package's payload
open(sys.argv[2], "wb").write(data)
EOF
pkgutil --expand "$candidate" "$work/expanded"
echo "<!-- edited -->" >> "$work/expanded/Distribution"
pkgutil --flatten "$work/expanded" "$work/repacked.pkg"

manifest "$work/flipped.pkg" "$(shasum -a 256 "$candidate" | awk '{print $1}')"
refused "a download that doesn't match its checksum" "didn't match its checksum"
test ! -e "$b/data/updates/Butler-$new.pkg" || fail "the mismatched download was kept"
test -f "$b/data/updates/Butler-$old.pkg" || fail "the rollback copy of $old wasn't fetched from its GitHub Release"
manifest "$work/flipped.pkg"
refused "a package with a changed byte" "isn't signed by Butler's publisher"
manifest "$work/repacked.pkg"
refused "a repacked, unsigned package" "isn't signed by Butler's publisher"

step "update $old → $new"
manifest "$candidate"
"$b/bin/butler" update --check | tee "$work/check.log"
grep -q "Version $new is ready" "$work/check.log" || fail "--check didn't see $new"
"$b/bin/butler" update --yes | tee "$work/update.log"
grep -q "Done. You're on version $new." "$work/update.log" || fail "the update didn't report success"

step "after the update"
[ "$(cat "$b/.butler-version")" = "$new" ] || fail "the version file says $(cat "$b/.butler-version")"
"$b/bin/butler" update --check | tee "$work/after.log"
grep -q "You're up to date: version $new." "$work/after.log" || fail "butler doesn't report $new"
fingerprint | diff - "$work/household.before" || fail "the household's files changed"
echo "household files: byte for byte the same"
test ! -e "$b/.butler-unsigned-build" || fail "$new carries the unsigned-build marker"
codesign --verify --strict "$b/bin/butler-ek" || fail "the Calendar helper's signature doesn't verify"
helper="$(codesign -dv "$b/bin/butler-ek" 2>&1)"
grep -q "TeamIdentifier=VB9CHJM4AN" <<< "$helper" || { echo "$helper"; fail "the Calendar helper isn't signed by our team"; }
problems="$(py 'from butler import updates; print("; ".join(updates.health()))')"
[ -z "$problems" ] || fail "health check: $problems"
problem="$(py 'from butler import updates; print(updates.brief_problem() or "")')"
[ -z "$problem" ] || fail "brief check: $problem"
echo "health check: passed, the brief included"
test -f "$b/data/updates/Butler-$old.pkg" || fail "the rollback package isn't kept"
test -f "$b/data/updates/Butler-$new.pkg" || fail "the installed package isn't kept"
line="$(py 'from butler import updates; print(updates.brief_lines("Bento"))')"
[[ "$line" == *"Bento updated overnight to $new."* ]] || fail "brief line: $line"
echo "brief line: $line"

echo
echo "signed to signed, $old → $new: all good"
