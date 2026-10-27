"""What the persona harness reads off a screen, without the paid libraries, so the free suite can
check it against Butler (tests/test_personas.py)."""
import re
import unicodedata

# `butler claude` with BUTLER_CLAUDE_DRY_RUN prints what it would run (src/butler/cli.py,
# open_claude); the harness opens that Claude Code session for the persona instead.
# The window's prompt comes straight after it, so it ends at the line's end, not the screen's.
DRY_CLAUDE = re.compile(r'\(dry run\) would run: (\S+) --permission-mode (\S+) "(.*)" in (\S+)[ \t]*\r?\n', re.S)

# The homepage's name box (website src/lib/butler-name.ts, src/scripts/download-hook.ts): it takes
# at most 24 characters, says this under a name that breaks the rules, and then downloads Butler.
MAX_NAME = 24
NAME_ERROR = "That name won't work. Use letters, numbers, spaces, hyphens or apostrophes, up to 24 characters."


def site_name(typed: str) -> tuple[str, str]:
    """(the downloaded file's name, without its extension; what the page says), for what a
    person typed into the homepage's name box."""
    typed = typed[:MAX_NAME]
    name = re.sub(r"\s+", " ", unicodedata.normalize("NFC", typed).strip())
    ok = bool(name) and (name[0].isalnum()) and all(c.isalnum() or c in " '\u2019-" for c in name)
    if ok:
        return name, ""
    return "Butler", NAME_ERROR if typed.strip() else ""


def page_text(html: str) -> str:
    """A web page as the persona reads it: the words, without scripts, styles or tags."""
    html = re.sub(r"<(script|style)[\s\S]*?</\1>", "", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def side_question(messages: list, question: str) -> list:
    """The persona's conversation, with a question for it mid-action: Claude Code's permission
    prompt or multiple-choice question, which comes while the persona's claude_code call is still
    running. The API needs a result for every pending tool call before anything else, so each gets
    a placeholder, then the question follows. Nothing before is changed, so it stays cached."""
    last = messages[-1]
    blocks = last["content"] if isinstance(last["content"], list) else []
    if last["role"] == "assistant":
        pending = [b.id if hasattr(b, "id") else b["id"] for b in blocks
                   if (b.type if hasattr(b, "type") else b.get("type")) == "tool_use"]
        content = [{"type": "tool_result", "tool_use_id": i, "content": "(Waiting: Claude Code is asking you something first.)"}
                   for i in pending] + [{"type": "text", "text": question}]
        return messages + [{"role": "user", "content": content}]
    text = last["content"] if isinstance(last["content"], str) else None
    content = ([{"type": "text", "text": text}] if text is not None else list(last["content"])) + [{"type": "text", "text": question}]
    return messages[:-1] + [{"role": "user", "content": content}]
