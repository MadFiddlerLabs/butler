#!/bin/bash
# Builds the Butler installer package from this checkout's committed files.
#
#   packaging/macos/build-pkg.sh --version v1.2.3 [--out dist] [--helper bin/butler-ek]
#                                [--app Butler.app] [--sign "Developer ID Installer: … (TEAMID)"]
#
# The package installs into the current user's home (~/Butler, and the Butler app that
# build-app.sh makes into ~/Applications), without an admin password. postinstall then moves the
# app to /Applications when the account is an admin (scripts/lib/install.sh). It carries only tracked
# files, so an upgrade never touches the household's config, data, projects or budget (all
# git-ignored). postinstall names the butler from the package's filename and opens the guided
# first run, or, over an existing Butler, the choice to resume or start fresh. Without --sign the
# result is Butler-unsigned.pkg: fine for tests, but macOS warns before opening it.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
version="v0.0.0-dev"; out="$repo/dist"; helper=""; app=""; sign=""
while [ $# -gt 0 ]; do
  case "$1" in
    --version) version="$2"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    --helper) helper="$2"; shift 2 ;;
    --app) app="$2"; shift 2 ;;
    --sign) sign="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
number="${version#v}"
number="${number%%-*}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
stage="$work/root/Butler"
mkdir -p "$stage" "$work/scripts" "$out"
# Through a file, not a pipe: macOS tar can stop reading at the archive's end marker while git
# still writes padding, and git then dies of SIGPIPE (exit 141) and fails the build at random.
git -C "$repo" archive --format=tar -o "$work/source.tar" HEAD
tar -x -f "$work/source.tar" -C "$stage"
rm -rf "$stage/.github" "$stage/packaging"
printf '%s\n' "$version" > "$stage/.butler-version"
# Unsigned test builds name their version in a marker. Only then can a test relax the updater's
# signature check (see src/butler/updates.py); signed packages never carry it.
[ -n "$sign" ] || printf '%s\n' "$version" > "$stage/.butler-unsigned-build"
if [ -n "$helper" ]; then
  install -m 755 "$helper" "$stage/bin/butler-ek"
fi
if [ -n "$app" ]; then
  mkdir -p "$work/root/Applications"
  ditto "$app" "$work/root/Applications/Butler.app"
fi
install -m 755 "$here/scripts/postinstall" "$work/scripts/postinstall"
install -m 644 "$repo/scripts/lib/names.sh" "$work/scripts/names.sh"
install -m 644 "$repo/scripts/lib/install.sh" "$work/scripts/install.sh"

# The app always lands in ~/Applications/Butler.app (postinstall may then move it). Without this, Installer would "relocate" it
# into any other copy with the same identifier it finds on the Mac.
pkgbuild --analyze --root "$work/root" "$work/component.plist" >/dev/null
i=0
while plutil -extract "$i" xml1 -o - "$work/component.plist" >/dev/null 2>&1; do
  plutil -replace "$i.BundleIsRelocatable" -bool NO "$work/component.plist"
  i=$((i + 1))
done
pkgbuild --root "$work/root" --component-plist "$work/component.plist" --identifier com.buildyourbutler.butler \
  --version "$number" --install-location / --scripts "$work/scripts" --ownership preserve \
  "$work/Butler-component.pkg" >/dev/null
sed "s/__VERSION__/$number/" "$here/distribution.xml" > "$work/distribution.xml"
if [ -n "$sign" ]; then
  name="Butler.pkg"
  productbuild --distribution "$work/distribution.xml" --resources "$here/resources" \
    --package-path "$work" --sign "$sign" --timestamp "$out/$name" >/dev/null
else
  name="Butler-unsigned.pkg"
  productbuild --distribution "$work/distribution.xml" --resources "$here/resources" \
    --package-path "$work" "$out/$name" >/dev/null
fi
echo "$out/$name"
