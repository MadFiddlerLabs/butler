# shellcheck shell=bash
# Butler's own uv, pinned by version and SHA-256 (docs/UPDATES.md, "Dependencies").
# Sourced by scripts/bootstrap.sh and scripts/lock.sh. uv also checks the Python it downloads
# against checksums built into it, so pinning uv pins Butler's Python too.
#
# To move to a newer uv: change BUTLER_UV_VERSION and the checksums below, from
# https://github.com/astral-sh/uv/releases/download/<version>/uv-<target>.tar.gz.sha256
# (.zip.sha256 for Windows).
BUTLER_UV_VERSION="0.13.0"

butler_uv_sha256() {  # butler_uv_sha256 <target>
  case "$1" in
    aarch64-apple-darwin) echo "a9c1b29002cf3c83f07fa9cd8a887a3be0107d90e23189721221e7257db8e3d6" ;;
    x86_64-apple-darwin) echo "5f44dcbde809b632f47c36fadb241cb4d6f9af71d0c8f172b5d2026d3dde742c" ;;
    x86_64-unknown-linux-gnu) echo "1468ebd5a5541121837c5a2817b9972ba6090fa6caa3d142620850a47fb75154" ;;  # CI's lock check
    # Windows (scripts/bootstrap.ps1 reads these lines): the .zip builds.
    x86_64-pc-windows-msvc) echo "088962f9e7b7bd9ea740c04c650b2a21c8928c345bd99ac24350dc924dba656c" ;;
    aarch64-pc-windows-msvc) echo "cb54028b59aa87f11cf6dec293ce000420043a49a8aea522e822a671321f8932" ;;
    *) return 1 ;;
  esac
}

butler_uv_target() {
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) echo "aarch64-apple-darwin" ;;
    Darwin-x86_64) echo "x86_64-apple-darwin" ;;
    Linux-x86_64) echo "x86_64-unknown-linux-gnu" ;;
    *) return 1 ;;
  esac
}

# butler_uv <dir>: prints the path of a uv at BUTLER_UV_VERSION. One already on this Mac at that
# version is used as it is; otherwise the pinned build is downloaded into <dir>, and only if its
# SHA-256 matches. Messages go to stderr, so the path is the only output.
butler_uv() {
  local dir="$1" have target sha tmp got
  for have in "$dir/uv" "$(command -v uv || true)" "$HOME/.local/bin/uv"; do
    if [ -n "$have" ] && [ -x "$have" ] && [ "$("$have" --version 2>/dev/null | awk '{print $2}')" = "$BUTLER_UV_VERSION" ]; then
      echo "$have"
      return 0
    fi
  done
  target="$(butler_uv_target)" || { echo "Butler has no uv for $(uname -sm)." >&2; return 1; }
  sha="$(butler_uv_sha256 "$target")"
  echo "Getting uv $BUTLER_UV_VERSION, which gives Butler its own Python (https://docs.astral.sh/uv/)." >&2
  tmp="$(mktemp -d)"
  if ! curl -fsSL --retry 3 -o "$tmp/uv.tar.gz" \
      "https://github.com/astral-sh/uv/releases/download/$BUTLER_UV_VERSION/uv-$target.tar.gz"; then
    rm -rf "$tmp"; echo "Couldn't download uv. Check the internet connection and try again." >&2; return 1
  fi
  got="$(shasum -a 256 "$tmp/uv.tar.gz" | awk '{print $1}')"
  if [ "$got" != "$sha" ]; then
    rm -rf "$tmp"; echo "The uv download didn't match its checksum, so it wasn't used." >&2; return 1
  fi
  tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
  mkdir -p "$dir"
  install -m 755 "$tmp/uv-$target/uv" "$dir/uv"
  rm -rf "$tmp"
  echo "$dir/uv"
}
