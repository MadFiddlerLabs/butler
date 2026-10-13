#!/bin/bash
# Over-the-air updates end to end on a Mac, with the real Installer: install an old package into
# this home, add a household's files, update to a new package from a local manifest, then try a
# package that doesn't load and one that loads but breaks the brief (both rolled back), and an
# unsigned one without the test flag (refused).
#
# CI only: it installs into ~/Butler. Packages are unsigned test builds, so the signature check
# is relaxed with BUTLER_UPDATE_ALLOW_UNSIGNED, which works only for a test build's own version
# (tests/test_updates.py proves a real install ignores it).
set -euo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
b="$HOME/Butler"
fail() { echo "FAIL: $*" >&2; exit 1; }
step() { echo; echo "== $*"; }
[ ! -e "$b" ] || fail "$b exists already; this test needs a clean home"

build() {  # build <version> <checkout> → prints the package path
  "$1/packaging/macos/build-pkg.sh" --version "$2" --out "$work/build-$2" | tail -n 1
}
release() {  # release <version> <pkg>: where the updater fetches a rollback copy
  mkdir -p "$work/releases/$1" && cp "$2" "$work/releases/$1/Butler.pkg"
}
manifest() {  # manifest <version> <pkg>
  local sha size
  sha="$(shasum -a 256 "$2" | awk '{print $1}')"; size="$(stat -f %z "$2")"
  printf '{"available":true,"version":"%s","pkg_url":"%s","sha256":"%s","size":%s,"notes":"Test release %s."}\n' \
    "$1" "$2" "$sha" "$size" "$1" > "$work/stable.json"
}
fingerprint() {  # the household's own files, byte for byte
  (cd "$b" && shasum -a 256 data/household_notes.md projects/home.yaml budget/budget.xlsx)
}

step "build v0.0.1, v0.0.2, a broken v0.0.3, a good v0.0.4 and a v0.0.5 that breaks the brief"
v1="$(build "$repo" v0.0.1)"; v2="$(build "$repo" v0.0.2)"; v4="$(build "$repo" v0.0.4)"
git clone -q "$repo" "$work/broken"
printf '\ndef broken(:\n' >> "$work/broken/src/butler/render.py"
git -C "$work/broken" -c user.name=ci -c user.email=ci@example.com commit -qam "a broken release"
v3="$(build "$work/broken" v0.0.3)"
# Every module loads, but the brief crashes as it's made: only the brief check catches it.
git clone -q "$repo" "$work/crashes"
printf '\n\ndef to_text(*args, **kwargs):\n    raise TypeError("a release that breaks at run time")\n' \
  >> "$work/crashes/src/butler/render.py"
git -C "$work/crashes" -c user.name=ci -c user.email=ci@example.com commit -qam "a release that crashes"
v5="$(build "$work/crashes" v0.0.5)"
release v0.0.1 "$v1"

step "install v0.0.1 with Installer, then a household's files"
installer -pkg "$v1" -target CurrentUserHomeDirectory
[ "$(cat "$b/.butler-version")" = "v0.0.1" ] || fail "v0.0.1 not installed"
grep -v -e '^updates:' -e '^update_hour:' "$b/config/settings.example.yaml" > "$b/config/settings.yaml"
mkdir -p "$b/data" "$b/projects"
echo "- the Johnsons' plumber is Rui (invented)" > "$b/data/household_notes.md"
cp "$b/examples/projects/home.yaml" "$b/projects/home.yaml"
cp "$b/budget/budget.example.xlsx" "$b/budget/budget.xlsx"
cp "$b/config/settings.yaml" "$work/settings.before"
fingerprint > "$work/household.before"
"$b/scripts/bootstrap.sh"

# BUTLER_COLUMNS: no wrapping, so the greps below see whole sentences. BUTLER_API: nothing listens
# there, so these test updates never report an outcome to the real website (update_report.py).
export BUTLER_RELEASES_URL="$work/releases" BUTLER_UPDATE_MANIFEST="$work/stable.json" BUTLER_COLUMNS=1000 BUTLER_API=http://127.0.0.1:9/api

step "update to v0.0.2"
manifest v0.0.2 "$v2"
"$b/bin/butler" update --check | tee "$work/check.log"
grep -q "Version v0.0.2 is ready" "$work/check.log" || fail "--check didn't see v0.0.2"
BUTLER_UPDATE_ALLOW_UNSIGNED=1 "$b/bin/butler" update --yes | tee "$work/update.log"
[ "$(cat "$b/.butler-version")" = "v0.0.2" ] || fail "v0.0.2 not installed"
fingerprint | diff - "$work/household.before" || fail "the household's files changed"
head -c "$(stat -f %z "$work/settings.before")" "$b/config/settings.yaml" | cmp - "$work/settings.before" \
  || fail "settings.yaml was rewritten, not appended to"
grep -q "^updates: auto" "$b/config/settings.yaml" || fail "migration 1 didn't add the updates setting"
grep -q '"applied": \[' "$b/data/migrations.json" && grep -q "1" "$b/data/migrations.json" || fail "no migration record"
test -f "$b/data/updates/Butler-v0.0.1.pkg" || fail "the rollback package isn't kept"
test -f "$b/data/updates/Butler-v0.0.2.pkg" || fail "the installed package isn't kept"
line="$(cd "$b" && PYTHONPATH=src .venv/bin/python -c 'from butler import updates; print(updates.brief_lines("Bento"))')"
[[ "$line" == *"Bento updated overnight to v0.0.2. What's new: Test release v0.0.2."* ]] || fail "brief line: $line"
cd "$b" && PYTHONPATH=src .venv/bin/python -c 'from butler import updates; updates.mark_announced()' && cd - >/dev/null

step "a broken v0.0.3 fails its check and goes back to v0.0.2"
manifest v0.0.3 "$v3"
if BUTLER_UPDATE_ALLOW_UNSIGNED=1 "$b/bin/butler" update --yes > "$work/broken.log" 2>&1; then
  cat "$work/broken.log"; fail "the broken update reported success"
fi
cat "$work/broken.log"
grep -q "went back to v0.0.2" "$work/broken.log" || fail "no rollback message"
[ "$(cat "$b/.butler-version")" = "v0.0.2" ] || fail "not back on v0.0.2"
fingerprint | diff - "$work/household.before" || fail "the household's files changed during rollback"
(cd "$b" && PYTHONPATH=src .venv/bin/python -c 'import butler.render') || fail "v0.0.2's code wasn't restored"
test ! -e "$b/data/updates/Butler-v0.0.3.pkg" || fail "the broken package was kept"
line="$(cd "$b" && PYTHONPATH=src .venv/bin/python -c 'from butler import updates; print(updates.brief_lines("Bento"))')"
[[ "$line" == *"went back to v0.0.2"* ]] || fail "brief line after rollback: $line"

step "a v0.0.5 that loads but breaks the brief fails its check and goes back to v0.0.2"
cd "$b" && PYTHONPATH=src .venv/bin/python -c 'from butler import updates; updates.mark_announced()' && cd - >/dev/null
manifest v0.0.5 "$v5"
if BUTLER_UPDATE_ALLOW_UNSIGNED=1 "$b/bin/butler" update --yes > "$work/crashes.log" 2>&1; then
  cat "$work/crashes.log"; fail "the update that breaks the brief reported success"
fi
cat "$work/crashes.log"
grep -q "went back to v0.0.2" "$work/crashes.log" || fail "no rollback message"
[ "$(cat "$b/.butler-version")" = "v0.0.2" ] || fail "not back on v0.0.2"
problem="$(cd "$b" && PYTHONPATH=src .venv/bin/python -c 'from butler import updates; print(updates.load_state()["last_result"]["problem"])')"
[[ "$problem" == *"the morning brief didn't build: the brief (TypeError: a release that breaks at run time)"* ]] \
  || fail "the problem recorded: $problem"
fingerprint | diff - "$work/household.before" || fail "the household's files changed during rollback"
(cd "$b" && PYTHONPATH=src .venv/bin/python -c 'from butler import daily; assert not daily.brief_check()["failures"]') \
  || fail "v0.0.2's brief doesn't build after going back"
test ! -e "$b/data/updates/brief-baseline.json" || fail "the baseline was left behind"

step "an unsigned v0.0.4 without the test flag is refused"
manifest v0.0.4 "$v4"
if "$b/bin/butler" update --yes > "$work/unsigned.log" 2>&1; then
  cat "$work/unsigned.log"; fail "an unsigned package was installed"
fi
grep -q "isn't signed by Butler's publisher" "$work/unsigned.log" || { cat "$work/unsigned.log"; fail "wrong refusal"; }
[ "$(cat "$b/.butler-version")" = "v0.0.2" ] || fail "the version changed"

echo
echo "updates end to end: all good"
