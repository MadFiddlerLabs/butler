#!/bin/bash
# Double-clickable: the guided first run in Terminal. The installer opens this file on a fresh
# install. When the first run ends, the window stays open with `butler` ready to type.
"$(dirname "$0")/first-run.sh"
exec "${SHELL:-/bin/zsh}" -l
