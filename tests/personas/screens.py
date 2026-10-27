"""What the persona harness reads off a screen, without the paid libraries, so the free suite can
check it against Butler (tests/test_personas.py)."""
import re

# `butler claude` with BUTLER_CLAUDE_DRY_RUN prints what it would run (src/butler/cli.py,
# open_claude); the harness opens that Claude Code session for the persona instead.
DRY_CLAUDE = re.compile(r'\(dry run\) would run: (\S+) --permission-mode (\S+) "(.*)" in (\S+)\s*$', re.S)


def page_text(html: str) -> str:
    """A web page as the persona reads it: the words, without scripts, styles or tags."""
    html = re.sub(r"<(script|style)[\s\S]*?</\1>", "", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
