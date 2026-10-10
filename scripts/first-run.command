#!/bin/bash
# Double-clickable: opens the guided first run in Terminal. The installer opens this file.
exec "$(dirname "$0")/first-run.sh"
