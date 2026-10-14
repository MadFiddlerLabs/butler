#!/bin/bash
# Builds the Butler app for the package: a tiny bundle whose one universal binary opens Terminal on
# ~/Butler/scripts/butler.command (helpers/butler-app/main.swift). The package installs it in
# ~/Applications, so students can find their butler again from Launchpad, Spotlight or Finder.
# With a signing identity it is signed with Developer ID under the hardened runtime, as the
# Calendar helper is, so the notarized package carries it; without one, ad hoc.
#
#   packaging/macos/build-app.sh <output Butler.app> [version] ["Developer ID Application: … (TEAMID)"]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
app="$1"
version="${2:-0.0.0}"
version="${version#v}"
version="${version%%-*}"
identity="${3:-}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

rm -rf "$app"
mkdir -p "$app/Contents/MacOS"
sed "s/__VERSION__/$version/g" "$repo/helpers/butler-app/app-Info.plist" > "$app/Contents/Info.plist"
plutil -lint "$app/Contents/Info.plist" >/dev/null
for arch in arm64 x86_64; do
  swiftc -O -target "$arch-apple-macos14" "$repo/helpers/butler-app/main.swift" -o "$tmp/Butler-$arch"
done
lipo -create -output "$app/Contents/MacOS/Butler" "$tmp/Butler-arm64" "$tmp/Butler-x86_64"
if [ -n "$identity" ]; then
  codesign --force --options runtime --timestamp --identifier com.buildyourbutler.butler-app \
    --sign "$identity" "$app"
else
  codesign --force --sign - --identifier com.buildyourbutler.butler-app "$app"
fi
codesign --verify --strict "$app"
echo "built $app ($(lipo -archs "$app/Contents/MacOS/Butler"))"
