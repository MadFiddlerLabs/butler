#!/bin/bash
# The installer opens this file when Butler was already on this Mac: resume where you left off
# (the default), or start fresh. Then the window stays open with `butler` ready to type.
"$(dirname "$0")/first-run.sh" --again
exec "${SHELL:-/bin/zsh}" -l
