#!/bin/bash
# Plain output for a screen reader: words only, with no colour, tick marks or middle dots that
# VoiceOver reads aloud or skips. Sourced by scripts/first-run.sh; butler.config.plain_output
# decides the same way for the `butler` command.
#
# On with BUTLER_PLAIN=1, when CLAUDE_AX_SCREEN_READER is set (the course's accessibility page has
# VoiceOver users set it for Claude Code), or while VoiceOver runs. BUTLER_PLAIN=0 turns it off.

butler_plain() {
  case "$(printf '%s' "${BUTLER_PLAIN:-}" | tr '[:upper:]' '[:lower:]')" in
    1|yes|true) return 0 ;;
    0|no|false) return 1 ;;
  esac
  case "$(printf '%s' "${CLAUDE_AX_SCREEN_READER:-}" | tr '[:upper:]' '[:lower:]')" in
    ''|0|no|false) ;;
    *) return 0 ;;
  esac
  pgrep -x VoiceOver >/dev/null 2>&1
}
