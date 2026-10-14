#!/bin/bash
# The Butler app (~/Applications/Butler.app) opens this file: where you are in the course and
# the one thing to do next, then the window stays open with `butler` ready to type.
"$(dirname "$0")/../bin/butler"
exec "${SHELL:-/bin/zsh}" -l
