"""What the persona harness reads off a screen, without the paid libraries, so the free suite can
check it against Butler (tests/test_personas.py)."""
import html as html_lib
import json
import os
import re
import unicodedata
from pathlib import Path

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


# What Butler says where it would open a module's video, with BUTLER_NO_OPEN (course_video.open_page,
# and scripts/first-run.sh for Module 0): a runner's student account has no browser to open.
VIDEO_LINE = re.compile(r"Module (\d+)'s video, (.+?), is here:\s+(https://buildyourbutler\.com/course/[\w-]+/#video)")


def videos_in(text: str) -> list[tuple[int, str]]:
    """The module videos Butler opened in this text: (module, title)."""
    return [(int(m[1]), m[2]) for m in VIDEO_LINE.finditer(text)]


def as_opened(text: str) -> str:
    """The text as a student's screen shows it, where Butler opened the video in their browser."""
    return VIDEO_LINE.sub(lambda m: f"I've opened Module {m[1]}'s video in your browser: {m[2]}.", text)


def watching(video: dict) -> str:
    """A module's video (lessons.mjs's `videos`), as the persona takes it in: scene by scene, what's
    on screen and what the voice says."""
    scenes = "\n".join(f"- On screen: {s['shown']}\n  The voice: {s['said']}" for s in video["scenes"])
    return f"You watch Module {video['module']}'s video, {video['title']} (about {video['minutes']} minutes):\n\n{scenes}"


def page_text(html: str) -> str:
    """A web page as the persona reads it: the words, without scripts, styles or tags, and with
    entities decoded as a browser shows them (round 6 saw "Ukraine&#x27;s" and called it a bug)."""
    html = re.sub(r"<(script|style)[\s\S]*?</\1>", "", html)
    return html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip())


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


# The persona's conversation is trimmed once its screens pass this many characters: older screens
# keep their first lines. Rarely, so the prompt cache is rebuilt only then: round 3 spent $5-6 a
# persona rereading old screens. The recent messages are never trimmed (at most 6 screens of
# 8,000 characters, so they alone can't set it off every turn).
SCREENS_KEPT = 120_000
RECENT = 12
TRIMMED = "\n… (an earlier screen, trimmed)"


def _screens(messages: list) -> list[dict]:
    return [b for m in messages if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"
            and isinstance(b.get("content"), str)]


def trim(messages: list) -> bool:
    """Once the untrimmed screens add up to SCREENS_KEPT characters, each one older than the last
    RECENT messages keeps only its first 300. The persona still knows what it did; it rereads
    less. True when it trimmed."""
    if sum(len(b["content"]) for b in _screens(messages) if not b["content"].endswith(TRIMMED)) < SCREENS_KEPT:
        return False
    for b in _screens(messages[:-RECENT]):
        if not b["content"].endswith(TRIMMED) and len(b["content"]) > 300 + len(TRIMMED):
            b["content"] = b["content"][:300] + TRIMMED
    return True


def trust_folder(folder, config: Path | None = None) -> None:
    """Record that the persona trusted `folder`, where interactive Claude Code keeps it (~/.claude.json,
    or $CLAUDE_CONFIG_DIR/.claude.json). An SDK session never shows the trust question, and until a
    folder is trusted it ignores the folder's own allow rules (code.claude.com/docs/en/permissions,
    "What runs before you trust a folder"). A student who chose Yes gets them, so the persona must too."""
    if config is None:
        base = os.environ.get("CLAUDE_CONFIG_DIR")
        config = Path(base) / ".claude.json" if base else Path.home() / ".claude.json"
    try:
        data = json.loads(config.read_text())
    except (OSError, ValueError):
        data = {}
    projects = data.setdefault("projects", {})
    for key in {str(folder), Path(folder).as_posix()}:   # Windows keys may use either slash
        projects.setdefault(key, {})["hasTrustDialogAccepted"] = True
    config.write_text(json.dumps(data, indent=2))


# ---------- the real `butler claude` ----------
# The lines Butler prints as it starts Claude Code (cli.open_claude). The harness cuts the window
# there: what follows is Claude Code's own first screen, which the Agent SDK session takes over.
# tests/test_personas.py checks them against cli.py, so change both together.
CLAUDE_OPENING = re.compile(
    r"If\s+it\s+asks\s+you\s+to\s+sign\s+in,\s+follow\s+the\s+link\s+it\s+shows\.[^\n]*\n"
    r"(?:[ \t]*It\s+(?:starts\s+with\s+Module\s+1:\s+Meet\s+Claude\s+Code|picks\s+up\s+where\s+you\s+left\s+off\s+in\s+the\s+course)\.[^\n]*\n)?")
_CLAUDE_EXE = re.compile(r"(^|[\\/])claude(\.exe)?$", re.I)


def butler_claude(argv: list[str]) -> dict | None:
    """{"exe", "mode", "prompt"} when argv is the Claude Code `butler claude` started (the binary,
    --permission-mode and the opening prompt), else None: the Agent SDK's own sessions run the same
    binary with other arguments."""
    if len(argv) != 4 or not _CLAUDE_EXE.search(argv[0]) or argv[1] != "--permission-mode":
        return None
    return {"exe": argv[0], "mode": argv[2], "prompt": argv[3]}


def mac_claude(ps_args: str) -> dict | None:
    """The same, from a Mac's `ps -o args=` line, which joins the arguments with spaces: the home
    folder may have one ("/Users/byb test"), and the prompt has many."""
    if "--output-format" in ps_args:
        return None
    m = re.match(r"^(?P<exe>.*?[\\/]claude) --permission-mode (?P<mode>\S+) (?P<prompt>.+)$", ps_args.strip(), re.S)
    return {"exe": m["exe"], "mode": m["mode"], "prompt": m["prompt"]} if m else None


def split_windows(line: str) -> list[str]:
    """A Windows command line split as CommandLineToArgvW splits it (subprocess.list2cmdline builds
    them): 2n backslashes before a quote are n backslashes and the quote counts; 2n+1 are n and a
    literal quote; "" inside quotes is a quote."""
    args, current, quoted, started, i = [], [], False, False, 0
    while i < len(line):
        c = line[i]
        if c == "\\":
            n = len(line[i:]) - len(line[i:].lstrip("\\"))
            i += n
            if line[i:i + 1] == '"':
                current.append("\\" * (n // 2))
                if n % 2:
                    current.append('"')
                    i += 1
            else:
                current.append("\\" * n)
            started = True
            continue
        if c == '"':
            if quoted and line[i + 1:i + 2] == '"':
                current.append('"')
                i += 1
            else:
                quoted = not quoted
            started = True
        elif c in " \t" and not quoted:
            if started:
                args.append("".join(current))
                current, started = [], False
        else:
            current.append(c)
            started = True
        i += 1
    if started:
        args.append("".join(current))
    return args


def terminal_types(path: str) -> str:
    """What Terminal types into a new window's login shell to run a .command file: the path, each
    special character escaped with a backslash, then "; exit;"."""
    return re.sub(r"([^\w/.\-+@%:,])", r"\\\1", path) + " ; exit;"


def install_log_opened(log: str) -> tuple[bool, str] | None:
    """(Terminal opened, the .command) from the package's postinstall line in logs/install.log:
    what the installer opened, or tried to open, at the end of the install. None if it didn't try."""
    found = re.findall(r"installed \(\w+\): (opened|could not open) Terminal with (\S+\.command)", log)
    return (found[-1][0] == "opened", found[-1][1]) if found else None


# A console's keys as WriteConsoleInputW takes them: (virtual key, character) for a named key,
# (None, character) for any other character, and "ctrl_c", which a console turns into a signal.
VK = {"\r": (0x0D, "\r"), "\n": (0x0D, "\r"), "\t": (0x09, "\t"), "\x7f": (0x08, "\b"), "\b": (0x08, "\b"),
      "\x1b[A": (0x26, "\0"), "\x1b[B": (0x28, "\0"), "\x1b[C": (0x27, "\0"), "\x1b[D": (0x25, "\0"),
      "\x1b": (0x1B, "\x1b")}


def console_keys(text: str) -> list:
    """The harness's keys (run_persona.KEYS) and typed text, one entry per key press."""
    out, i = [], 0
    while i < len(text):
        if text[i] == "\x03":
            out.append("ctrl_c")
            i += 1
            continue
        seq = next((s for s in VK if text.startswith(s, i) and len(s) > 1), None) or (text[i] if text[i] in VK else None)
        if seq:
            out.append(VK[seq])
            i += len(seq)
        else:
            out.append((None, text[i]))
            i += 1
    return out


# ---------- windows on screen (Windows) ----------
def window_form(title: str, controls: list[tuple[str, str, bool, bool]]) -> dict:
    """A window as the persona sees it, from its controls: (class name, text, enabled, visible).
    Buttons are what can be clicked; check boxes and radio buttons can be ticked; the rest is text.
    Inno Setup's controls are TNewButton, TNewCheckBox, TNewRadioButton, TNewStaticText and
    TNewMemo; a message box's are Button and Static."""
    text, buttons, boxes = [], [], []
    for cls, label, enabled, visible in controls:
        label = re.sub(r"</?a\b[^>]*>", "", label.replace("&", ""), flags=re.I).strip()   # a SysLink's <A> tags
        if not label or not visible:
            continue
        if re.search(r"CheckBox|RadioButton", cls, re.I):
            boxes.append(label)
        elif re.search(r"Button", cls, re.I):
            if enabled and label not in buttons:
                buttons.append(label)
        elif label not in text and label != title:
            text.append(label)
    return {"title": title, "text": "\n  ".join(text), "buttons": buttons, "boxes": boxes}


def waiting_only(form: dict) -> bool:
    """A page with nothing to do but wait (Installing..., with only Cancel): not a question."""
    return not form["buttons"] or form["buttons"] == ["Cancel"]


# The rehearsal's student clicks the way forward, as most people do.
FORWARD = ("Run anyway", "Run", "More info", "Install", "Next", "Finish", "Yes", "OK", "Allow", "Continue")


def forward(form: dict) -> dict:
    button = next((b for want in FORWARD for b in form.get("buttons", []) if b.lower() == want.lower()), "")
    return {"button": button or (form.get("buttons") or [""])[0], "text": "", "ticked": []}
