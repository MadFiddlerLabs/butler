#!/bin/bash
# The Butler app opens this file: `butler resume` goes straight to where you left off (the first
# run, or Claude Code on your next step). When that ends, the window stays open with `butler`
# ready to type.
"$(dirname "$0")/../bin/butler" resume
exec "${SHELL:-/bin/zsh}" -l
