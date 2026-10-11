#!/bin/bash
# Installs Butler, or updates its code: downloads the newest release to ~/Butler and sets up
# its Python. The course has Claude run it:
#
#   curl -fsSL https://raw.githubusercontent.com/nerolabs/butler/main/scripts/install.sh | bash
#
# Set BUTLER_HOME to install somewhere else. Running it again updates the code and never
# touches your settings, data, projects or budget: those files aren't in the download.
# BUTLER_SOURCE=<a local checkout> installs that instead of downloading (used by the tests).
set -euo pipefail
repo="nerolabs/butler"
home="${BUTLER_HOME:-$HOME/Butler}"

if [ "$(uname)" != "Darwin" ]; then
  echo "Butler runs on a Mac. A Windows version is on the waitlist at buildyourbutler.com." >&2
  exit 1
fi
if [ -d "$home/.git" ]; then
  echo "$home is a git checkout. Update it with: git -C \"$home\" pull" >&2
  exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp" "$tmp.tar"' EXIT
if [ -n "${BUTLER_SOURCE:-}" ]; then
  # A local checkout: its committed files, exactly what a release download contains.
  version="local"
  echo "Installing Butler from $BUTLER_SOURCE to $home"
  # Through a file, not a pipe: macOS tar can stop reading early and git then dies of SIGPIPE.
  git -C "$BUTLER_SOURCE" archive --format=tar -o "$tmp.tar" HEAD
  tar -x -f "$tmp.tar" -C "$tmp"
else
  # The newest release, or main until the first release exists.
  tag="$(curl -fsSL "https://api.github.com/repos/$repo/releases/latest" 2>/dev/null \
    | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n 1 || true)"
  if [ -n "$tag" ]; then
    url="https://codeload.github.com/$repo/tar.gz/refs/tags/$tag"; version="$tag"
  else
    url="https://codeload.github.com/$repo/tar.gz/refs/heads/main"; version="main"
  fi
  echo "Downloading Butler ($version) to $home"
  curl -fsSL "$url" | tar -xz -C "$tmp" --strip-components 1
fi
mkdir -p "$home"
cp -R "$tmp"/. "$home"/
echo "$version" > "$home/.butler-version"

"$home/scripts/bootstrap.sh"
echo
echo "Butler is installed in $home."
echo "Next: open that folder in Claude and type /butler-setup install"
