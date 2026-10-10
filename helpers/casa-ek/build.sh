#!/bin/bash
# Builds bin/casa-ek with an embedded Info.plist so macOS can show the
# Calendar and Reminders permission prompts for it.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
mkdir -p "$root/bin"
swiftc -O "$here/main.swift" -o "$root/bin/casa-ek" \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$here/Info.plist"
codesign --force --sign - --identifier com.casa.casa-ek "$root/bin/casa-ek"
echo "built $root/bin/casa-ek"
