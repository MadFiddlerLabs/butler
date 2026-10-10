#!/bin/bash
# Builds the Butler installer package from this checkout's committed files.
#
#   packaging/macos/build-pkg.sh --version v1.2.3 [--out dist] [--helper bin/butler-ek]
#                                [--sign "Developer ID Installer: … (TEAMID)"]
#
# The package installs into the current user's home (~/Butler), without an admin password. It
# carries only tracked files, so an upgrade never touches the household's config, data, projects
# or budget (all git-ignored). postinstall names the butler from the package's filename and opens
# the guided first run. Without --sign the result is Butler-unsigned.pkg: fine for tests, but
# macOS warns before opening it.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
version="v0.0.0-dev"; out="$repo/dist"; helper=""; sign=""
while [ $# -gt 0 ]; do
  case "$1" in
    --version) version="$2"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    --helper) helper="$2"; shift 2 ;;
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
git -C "$repo" archive HEAD | tar -x -C "$stage"
rm -rf "$stage/.github" "$stage/packaging"
printf '%s\n' "$version" > "$stage/.butler-version"
# Unsigned test builds name their version in a marker. Only then can a test relax the updater's
# signature check (see src/butler/updates.py); signed packages never carry it.
[ -n "$sign" ] || printf '%s\n' "$version" > "$stage/.butler-unsigned-build"
if [ -n "$helper" ]; then
  install -m 755 "$helper" "$stage/bin/butler-ek"
fi
install -m 755 "$here/scripts/postinstall" "$work/scripts/postinstall"
install -m 644 "$repo/scripts/lib/names.sh" "$work/scripts/names.sh"

pkgbuild --root "$work/root" --identifier com.buildyourbutler.butler --version "$number" \
  --install-location / --scripts "$work/scripts" --ownership preserve "$work/Butler-component.pkg" >/dev/null
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
