#!/bin/bash
# Builds the Calendar and Reminders helper for the package: one universal binary for Apple silicon
# and Intel Macs, so students never need the Xcode tools. With a signing identity it is signed with
# Developer ID under the hardened runtime (needed for notarization); without one, ad hoc.
#
#   packaging/macos/build-helper.sh <output> ["Developer ID Application: … (TEAMID)"]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out="$1"
identity="${2:-}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

for arch in arm64 x86_64; do
  swiftc -O -target "$arch-apple-macos14" "$repo/helpers/butler-ek/main.swift" -o "$tmp/butler-ek-$arch" \
    -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$repo/helpers/butler-ek/Info.plist"
done
mkdir -p "$(dirname "$out")"
lipo -create -output "$out" "$tmp/butler-ek-arm64" "$tmp/butler-ek-x86_64"
if [ -n "$identity" ]; then
  codesign --force --options runtime --timestamp --entitlements "$here/helper.entitlements" \
    --identifier com.buildyourbutler.butler-ek --sign "$identity" "$out"
else
  codesign --force --sign - --identifier com.buildyourbutler.butler-ek "$out"
fi
echo "built $out ($(lipo -archs "$out"))"
