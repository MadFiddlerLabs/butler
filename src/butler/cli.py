"""The `butler` command. Every course lesson is one line that calls it:

    butler town "Lisbon"          butler adults Sam Alex          butler brief

`butler setup` (the guided first run) is plain bash in scripts/first-run.sh on a Mac, so it starts
before Python exists, and first_run.py on Windows. Everything here speaks plain words: the people
typing these may never have used a terminal before.
"""

import argparse
import getpass
import html
import logging
import os
import shutil
import re
import socket
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config, course_video, doctor, progress, setup_settings as ss, system, updates
from .config import ROOT, plain_output

CLAUDE_INSTALL = "curl -fsSL https://claude.ai/install.sh | bash"
# Windows: Anthropic's PowerShell installer (code.claude.com/docs/en/setup), which puts claude.exe
# in %USERPROFILE%\.local\bin.
CLAUDE_INSTALL_WINDOWS = "irm https://claude.ai/install.ps1 | iex"
# The first time, Claude Code opens on Module 1 (Meet Claude Code): a welcome, nothing changed.
# After that, /butler-setup on its own works out where the student is in the course.
FIRST_CLAUDE_PROMPT = (
    "Hi! I've just set up my butler and I'm starting Module 1 of the Build Your Butler course, "
    "Meet Claude Code. Please welcome me in plain words: say what you are, what you can do in this "
    "Butler folder, and {manners}. Tell me that whenever I'm unsure what to do, I can ask you: what's next? "
    "Don't change any files yet. Then suggest one simple first question I could ask you."
)
# What the welcome promises depends on the student's choice (`claude_mode`), so it stays true.
MANNERS = {
    "ask": "that you'll always ask before changing anything",
    "auto": ("that I chose auto mode, so you'll act more on your own while a safety check reviews each "
             "step, and that I can switch back any time by typing butler claude-mode ask in Terminal"),
}
NEXT_CLAUDE_PROMPT = "/butler-setup"
# Every step done: no next lesson to start, so Claude Code asks what they'd like to change.
DONE_CLAUDE_PROMPT = (
    "Hi! My butler is all set up: I've done every setup step of the Build Your Butler course. "
    "Please greet me in a line or two, in plain words, and ask what I'd like to change or improve. "
    "Don't change any files yet."
)
# Said each time Claude Code opens, because it opens in the same window as Butler.
CLAUDE_HERE = ("Claude Code works in this window. Type to it in plain words, and it runs the commands for you. "
               "Whenever you're unsure, ask it: what's next?")
CLAUDE_WELCOMED = ROOT / "data" / "claude-welcomed"
# Written by the first run when it's over; the installer reads it too.
FIRST_RUN_DONE = ROOT / "data" / "first-run-done"
ASK_DOCTOR = "Open Claude Code (type: butler claude) and type /butler-doctor"


def _width() -> int:
    """Prose fits a new Terminal window: at most 80 columns, less if the window is narrower.
    BUTLER_COLUMNS overrides it (tests use it)."""
    if os.environ.get("BUTLER_COLUMNS", "").isdigit():
        return int(os.environ["BUTLER_COLUMNS"]) - 4
    return min(shutil.get_terminal_size((80, 24)).columns, 80) - 4


def say(line: str = "") -> None:
    """Prints wrapped prose. A `butler <command>` mentioned in a sentence stays on one line, so it
    can be copied whole; lines that start with spaces (commands to paste) are never wrapped."""
    if not line or line.startswith(" "):
        print(line, flush=True)
        return
    glued = re.sub(r'butler [a-z][\w-]*(?: "[^"]*")?', lambda m: m.group(0).replace(" ", "\x1f"), line)
    wrapped = textwrap.fill(glued, _width(), break_on_hyphens=False, break_long_words=False)
    print(wrapped.replace("\x1f", " "), flush=True)


def _butler(s: dict) -> str:
    name = s.get("name") or ss.PLACEHOLDER_NAME
    return "your butler" if name == ss.PLACEHOLDER_NAME else name


# ---------- lessons ----------

def cmd_name(args) -> int:
    name = ss.valid_name(" ".join(args.name))
    if not name:
        say("That name won't work. Use letters, numbers, spaces, hyphens or apostrophes, up to 24 characters.")
        say('For example: butler name "Bento"')
        return 1
    s = ss.load()
    ss.set_name(s, name)
    ss.save(s)
    progress.report("setup.name")
    say(f"Lovely. I'm {name} from now on.")
    return 0


def cmd_town(args) -> int:
    query = " ".join(args.town).strip()
    if not query:
        say('Tell me your town, like this: butler town "Lisbon"')
        return 1
    try:
        place = ss.find_town(query)
    except OSError:
        say("I couldn't look that up just now. Check your internet connection and try again.")
        return 1
    if not place:
        say(f"I couldn't find \"{query}\". Check the spelling, or try a bigger town nearby. "
            "If two towns share the name, add your state or country after a comma.")
        return 1
    s = ss.load()
    ss.set_town(s, place)
    ss.save(s)
    progress.report("setup.town")
    unit = "Fahrenheit" if s["weather"]["unit"] == "fahrenheit" else "Celsius"
    say(f"Got it: {place.label()}. Your timezone is {place.timezone}, and weather comes in {unit}.")
    if place.others:
        say(f'Not the right {place.name}? Add the state or country, like this: butler town "{place.others[0]}"')
    else:
        say(f"Not the right {place.name}? Add your state or country after a comma, then paste it again.")
    return 0


def cmd_adults(args) -> int:
    names = [ss.valid_name(n) for n in args.names]
    if not names or not all(names):
        say("Give me the first names of the adults the emails are for, like this: butler adults Sam Alex")
        return 1
    s = ss.load()
    ss.set_adults(s, names)
    ss.save(s)
    who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    say(f"Thank you. I work for {who}.")
    return 0


def _preview() -> list[str]:
    """`./run.sh preview`, or run.py's on Windows, where there is no bash."""
    if system.NAME == "windows":
        return [sys.executable, "-X", "utf8", str(ROOT / "run.py"), "preview"]
    return [str(ROOT / "run.sh"), "preview"]


def cmd_brief(args) -> int:
    s = ss.load()
    missing = []
    if not s["household"].get("adults"):
        missing.append("who I work for: butler adults Sam Alex")
    if not s.get("weather"):
        missing.append('where you live: butler town "Lisbon"')
    if missing:
        say("Before your first brief, tell me " + " and ".join(missing) + ".")
        return 1
    if not ss.SETTINGS.exists():
        ss.save(s)
    who = _butler(s)
    say(f"{who[0].upper() + who[1:]} is writing your brief. This takes about half a minute: nothing shows until it's ready.")
    proc = subprocess.run(_preview(), cwd=ROOT, capture_output=True, text=True)
    brief = ss.first_brief()
    if proc.returncode != 0 or not brief:
        (ROOT / "logs").mkdir(exist_ok=True)
        (ROOT / "logs" / "brief-errors.log").write_text(proc.stdout + proc.stderr)
        say("Something went wrong writing your brief. Run: butler doctor")
        return 1
    progress.report("setup.first-brief")
    if os.environ.get("BUTLER_NO_OPEN"):
        say(f"Your brief is ready: {brief}")
    else:
        say("Your first brief is open in your browser. The parts marked \"not connected yet\" "
            "are what you'll add in the course.")
    return 0


def _claude_path() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / ("claude.exe" if system.NAME == "windows" else "claude")
    return str(local) if local.exists() else None


def _ask(prompt: str) -> str:
    """One answer from the keyboard, lowercased. No keyboard (a closed input) counts as Enter."""
    try:
        return input(prompt).strip().lower()
    except EOFError:
        print(flush=True)
        return ""


MODE_WORDS = {
    "ask": "Ask first: Claude checks with you before each change.",
    "auto": "Auto: Claude acts more on its own, and a safety check reviews each step instead of you.",
}


def _choose_mode(s: dict) -> str:
    """Asks once how Claude Code should work, and keeps the answer in the settings."""
    say("One choice before Claude Code opens. How should it work with you?")
    say(f"1. {MODE_WORDS['ask']} (Recommended while you learn.)")
    say(f"2. {MODE_WORDS['auto']}")
    answer = _ask("Type 1 or 2, or press Enter for Ask first: ")
    mode = "auto" if answer in {"2", "auto"} else "ask"
    s["claude_mode"] = mode
    ss.save(s)
    say(f"{'Ask first' if mode == 'ask' else 'Auto'} it is. You can change it any time: "
        f"butler claude-mode {'auto' if mode == 'ask' else 'ask'}")
    return mode


def _installer() -> tuple[str, list[str]]:
    """Claude Code's install line, and how to run it."""
    if system.NAME == "windows":
        return CLAUDE_INSTALL_WINDOWS, ["powershell", "-NoProfile", "-Command", CLAUDE_INSTALL_WINDOWS]
    return CLAUDE_INSTALL, ["bash", "-c", CLAUDE_INSTALL]


def _watch_first(module: int, data: Path | None = None) -> None:
    """The module's video, opened before the module starts. A person at the keyboard presses Enter
    once they've watched it; with no keyboard (a script), Butler carries on."""
    for line in course_video.open_page(module, data):
        say(line)
    if sys.stdin is not None and sys.stdin.isatty():
        _ask(course_video.READY)


def cmd_watch(args) -> int:
    """`butler watch calendar` (a /butler-setup step) or `butler watch 3`: the module's video, opened
    once as the step starts. Claude asks the student to say when they've watched it. Opened before,
    it says so and opens nothing, unless --again."""
    steps, modules = progress.course_map(), progress.course_modules()
    wanted = (args.what or "").strip().lower()
    module = steps[wanted]["module"] if wanted in steps else int(wanted) if wanted.isdigit() else None
    if module not in modules:
        say("Say which step or module, like this: butler watch calendar (or: butler watch 3)")
        return 1
    if module in course_video.opened() and not args.again:
        say(f"Module {module}'s video was opened before: {course_video.url(module)}")
        say(f"To open it again: butler watch {wanted} --again")
        return 0
    for line in course_video.open_page(module):
        say(line)
    return 0


def cmd_claude(args) -> int:
    return open_claude(args.yes, args.dry_run)


def open_claude(yes: bool = False, dry_run: bool = False, opening: str | None = None) -> int:
    """Claude Code in the Butler folder, in the household's `claude_mode`, on the student's next
    step: Module 1's welcome the first time, `/butler-setup` after that, and, once every step is
    done, a question about what to change. Installs Claude Code first if it's missing. `opening`
    replaces the usual two lines about what opens (`butler resume` says it its own way)."""
    dry = dry_run or bool(os.environ.get("BUTLER_CLAUDE_DRY_RUN"))
    if not CLAUDE_WELCOMED.exists() and 1 not in course_video.opened(CLAUDE_WELCOMED.parent):
        _watch_first(1, CLAUDE_WELCOMED.parent)   # Meet Claude Code: what it is, and Ask first or Auto
    exe = _claude_path()
    if not exe:
        say("Next, I move into Claude Code: an AI helper that does the rest of the setup with you.")
        say("It needs a paid Claude plan (Pro or Max) and a one-time sign-in in your browser.")
        if not yes:
            answer = _ask("Install Claude Code now? Press Enter for yes, or type no: ")
            if answer in {"n", "no"}:
                say("No problem. When you're ready, type: butler claude")
                return 0
        line, argv = _installer()
        # All three personas stopped at this note, unexplained (30 Oct 2026). _claude_path looks in
        # ~/.local/bin itself, so the PATH doesn't matter here.
        say("Anthropic's installer may end with a note about your PATH. You can ignore it: I find Claude Code myself.")
        if dry:
            say(f"(dry run) would run: {line}")
            exe = "claude"
        else:
            if subprocess.run(argv).returncode != 0:
                say("The Claude Code installer didn't finish. Check your internet connection and run: butler claude")
                return 1
            exe = _claude_path()
            if not exe:
                say("Claude Code is installed, but I can't find it yet. Close this window, open Terminal again, "
                    "and type: butler claude")
                return 1
    s = ss.load()
    mode = ss.claude_mode(s) or ("ask" if yes else _choose_mode(s))
    progress.report("setup.claude", "started")
    first = not CLAUDE_WELCOMED.exists()
    if first:
        prompt = FIRST_CLAUDE_PROMPT.format(manners=MANNERS[mode])
    else:
        prompt = DONE_CLAUDE_PROMPT if position().stage == "done" else NEXT_CLAUDE_PROMPT
    if opening:
        say(f"{opening} If it asks you to sign in, follow the link it shows.")
    else:
        say("Opening Claude Code in your Butler folder. If it asks you to sign in, follow the link it shows.")
        say("It starts with Module 1: Meet Claude Code." if first else "It picks up where you left off in the course.")
    say(CLAUDE_HERE)
    # The flag wins over any settings file, so the student's choice holds whatever else is set.
    argv = [exe, "--permission-mode", ss.PERMISSION_MODES[mode], prompt]
    if dry:
        say(f"(dry run) would run: {' '.join(argv[:-1])} \"{prompt}\" in {ROOT}")
        return 0
    CLAUDE_WELCOMED.parent.mkdir(exist_ok=True)
    CLAUDE_WELCOMED.touch()
    os.chdir(ROOT)
    if system.NAME == "windows":
        # Windows has no exec: os.execv starts a new process and ends this one, and the window's
        # prompt comes back while Claude Code is still running. So Claude Code runs as a child.
        return subprocess.run(argv).returncode
    os.execv(exe, argv)
    return 0  # not reached


def cmd_claude_mode(args) -> int:
    """`butler claude-mode ask|auto`: how Claude Code works with you, from the next `butler claude`."""
    s = ss.load()
    wanted = (args.mode or "").strip().lower()
    if not wanted:
        mode = ss.claude_mode(s) or "ask"
        say(f"Claude Code is set to {MODE_WORDS[mode]}")
        say(f"To change it, type: butler claude-mode {'auto' if mode == 'ask' else 'ask'}")
        return 0
    if wanted not in ss.CLAUDE_MODES:
        say("Choose ask or auto, like this: butler claude-mode ask")
        return 1
    s["claude_mode"] = wanted
    ss.save(s)
    say(f"Done. {MODE_WORDS[wanted]}")
    say("It starts the next time you type: butler claude")
    say("Inside Claude Code, the line under the typing box shows the mode: \"manual mode on\" means "
        "it asks first, \"auto mode on\" means auto.")
    return 0


# ---------- looking around ----------

def _checks() -> list[tuple[str, bool, str]]:
    """(what, done, the line that does it), in course order."""
    s = ss.load()
    named = s.get("name", ss.PLACEHOLDER_NAME) != ss.PLACEHOLDER_NAME
    services = {name: ss.has_secret(name) for name in
                ("butler-account", "butler-anthropic", "butler-gmail", "butler-lunchmoney")}
    scheduled = system.scheduled()
    return [
        ("Signed in to the course", services["butler-account"], "butler setup"),
        ("Your butler has a name", named, 'butler name "Bento"'),
        ("Your town and weather", bool(s.get("weather")), 'butler town "Lisbon"'),
        ("Who the emails are for", bool(s["household"].get("adults")), "butler adults Sam Alex"),
        ("Your first brief", ss.first_brief() is not None, "butler brief"),
        ("Claude Code", _claude_path() is not None, "butler claude"),
        ("Calendar and Reminders", progress.CHECKS["calendar"](progress.mac_folder()),
         "/butler-setup calendar (in Claude Code)"),
        ("A Claude API key", services["butler-anthropic"], "/butler-setup brain"),
        ("Your butler's email", services["butler-gmail"], "/butler-setup inbox"),
        ("The daily schedule", scheduled, "/butler-setup inbox"),
        ("Money (optional)", services["butler-lunchmoney"], "/butler-setup money"),
        ("On your phone (optional)", bool(s.get("portal", {}).get("url")), "/butler-setup phone"),
    ]


def cmd_status(args) -> int:
    s = ss.load()
    say(f"Here's where {_butler(s)} stands:")
    say()
    next_step = None
    for what, done, how in _checks():
        # The words carry it: VoiceOver skips a lone "·", so the marks are only for the eye.
        state = "Done:  " if done else "To do: "
        say(f"  {state}{what}" if plain_output() else f"  {'✓' if done else '·'} {state}{what}")
        if not done and next_step is None:
            next_step = how
    say()
    say(f"Next: {next_step}" if next_step else "Everything is set up. Well done.")
    return 0


def first_run_done() -> bool:
    """The guided first run is over: its marker, or, for a Butler from before the marker, its
    three lessons done."""
    return FIRST_RUN_DONE.exists() or ss.done_steps() == ["town", "adults", "brief"]


@dataclass
class Position:
    """Where the student is in the course, from the same checks that mark it (progress.py)."""
    who: str                 # the butler's name, ready to start a sentence
    stage: str               # "first run" (not finished), "claude" (Module 1 next), "course", "done"
    through: int | None      # the last module done; None during the first run
    next: int | None         # the module to do next; None when every step is done


def position() -> Position:
    who = _butler(ss.load())
    who = who[0].upper() + who[1:]
    if not first_run_done():
        return Position(who, "first run", None, 0)
    if not CLAUDE_WELCOMED.exists():
        return Position(who, "claude", 0, 1)
    state = progress.load()
    done = set(state["steps"]) | set(progress.passed(progress.mac_folder(), skip=set(state["steps"])))
    through = 1
    for step, entry in progress.course_map().items():
        if step not in done:
            return Position(who, "course", through, entry["module"])
        through = entry["module"]
    return Position(who, "done", through, None)


def _module(n: int) -> str:
    return f"Module {n}, {progress.course_modules()[n]['title']}"


def where(p: Position | None = None) -> list[str]:
    """Where the student is in the course and the one thing to do next, in plain words."""
    p = p or position()
    if p.stage == "first run":
        return [f"{p.who}'s first run isn't finished yet.", "To pick up where you left off, type: butler setup"]
    if p.stage == "done":
        return [f"{p.who} is set up through {_module(p.through)}. Well done.",
                f"Next: {_module(12)}, on the course website: {progress.module_url(12)}"]
    lines = [f"{p.who} is set up through {_module(p.through)}.", f"Next: {_module(p.next)}. Type: butler claude"]
    if p.stage == "course":
        lines.append(f"The lesson page: {progress.module_url(p.next)}")
    return lines


def cmd_home(args) -> int:
    """`butler` on its own: the way back. Where you are, the one thing to do next, the commands."""
    for line in where():
        say(line)
    if not first_run_done() and sys.stdin.isatty():
        if _ask("Pick up where you left off now? Press Enter for yes, or type no: ") not in {"n", "no"}:
            _first_run()
    say(f"To go straight there in one step, {way_back()}, or type: butler resume")
    say()
    print_help()
    return 0


def way_back() -> str:
    """What opens Butler where the student left off, without typing: the Butler app on a Mac, the
    Start menu's Butler on Windows (packaging/windows/butler.iss), which both run `butler resume`."""
    return "open Butler from the Start menu" if system.NAME == "windows" else "open the Butler app"


def _first_run(*args: str) -> None:
    """The guided first run, in place of this command: first-run.sh on a Mac, first_run.py on
    Windows."""
    if system.NAME == "windows":
        from . import first_run
        sys.exit(first_run.main(list(args)))
    first_run = ROOT / "scripts" / "first-run.sh"
    os.execv(str(first_run), [str(first_run), *args])


def cmd_setup(args) -> int:
    """`butler setup [--again]`. On a Mac, bin/butler runs first-run.sh before Python starts, so
    this is the way in on Windows."""
    _first_run(*(["--again"] if args.again else []))
    return 0  # not reached


# What `butler secret` stores, and what to paste for each.
SECRETS = {
    "butler-anthropic": "Your Claude API key",
    "butler-gmail": "Your butler's Gmail app password",
    "butler-lunchmoney": "Your Lunch Money access token",
}


def cmd_secret(args) -> int:
    """`butler secret butler-anthropic`: a key or password into the password store, typed hidden.
    It never goes on a command line, in a file or on screen. scripts/store-secret.sh does the same
    with a Mac dialog. Spaces are removed: Google shows app passwords in groups of four."""
    service = args.service or ""
    if service not in SECRETS:
        say("Say which one to store, like this: butler secret butler-anthropic")
        say(f"It can be {', '.join(SECRETS)}.")
        return 1
    say(f"{SECRETS[service]}: paste it and press Enter. It stays hidden while you type.")
    try:
        secret = "".join(getpass.getpass(f"{SECRETS[service]} \u203a ").split())
    except (EOFError, KeyboardInterrupt):
        print(flush=True)
        say("Cancelled. Nothing was stored.")
        return 1
    if not secret:
        say("That was empty. Nothing was stored.")
        return 1
    try:
        config.store_secret(service, secret)
    except (OSError, ValueError, subprocess.SubprocessError):
        say(f"{system.PASSWORD_STORE} didn't take it. Nothing was stored. {ASK_DOCTOR}.")
        return 1
    say(f"Stored {service} in {system.PASSWORD_STORE}.")
    return 0


def _note(line: str) -> None:
    """One line in logs/setup.log, so `/butler-doctor` (and the end-to-end test) can tell what the
    Butler app opened."""
    try:
        (ROOT / "logs").mkdir(exist_ok=True)
        with open(ROOT / "logs" / "setup.log", "a") as f:
            f.write(f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {line}\n")
    except OSError:
        pass


def cmd_resume(args) -> int:
    """`butler resume`, what the Butler app runs: straight to where the student left off. The
    first run while it isn't finished; after it, Claude Code on their next step, exactly as
    `butler claude` opens it."""
    p = position()
    if p.stage == "first run":
        _note("resume: the first run")
        _first_run()
    if p.stage == "done":
        say(f"{p.who} is set up through {_module(p.through)}. Well done.")
        opening = "Opening Claude Code, so you can tell it what you'd like to change."
        _note("resume: Claude Code, every step done")
    else:
        say(f"{p.who} is set up through {_module(p.through)}.")
        opening = f"Opening Claude Code for {_module(p.next)}."
        _note(f"resume: Claude Code, Module {p.next}")
    return open_claude(opening=opening)


def cmd_progress(args) -> int:
    """What the course knows: each setup step, done or not, and whether it reached the account."""
    state, problem = progress.sync(force=True)
    say(f"Course progress {_butler(ss.load())} reports to your Build Your Butler account:")
    say()
    for line in progress.lines(state, problem):
        say(line)
    say()
    say("A step counts once its check passes. Your butler sends only lesson names and dates, "
        "nothing from your household.")
    return 0


def cmd_doctor(args) -> int:
    problems = []
    if sys.version_info < (3, 12):
        problems.append(("Python is too old for Butler.", ASK_DOCTOR))
    try:
        s = ss.load()
        for key in ("timezone", "email", "household", "calendar", "news", "llm", "portal"):
            if key not in s:
                problems.append((f"Your settings are missing '{key}'.", ASK_DOCTOR))
    except Exception as e:
        problems.append((f"Your settings file can't be read ({str(e).splitlines()[0]}).",
                         ASK_DOCTOR))
    log = ROOT / "logs" / "butler.log"
    if log.exists():
        recent = log.read_text(errors="replace").splitlines()[-300:]
        errors = [l for l in recent if " ERROR " in l]
        if errors:
            problems.append((f"The log has {len(errors)} recent error(s).",
                             ASK_DOCTOR + " to have them explained"))
        problems += doctor.log_problems(recent)
    problems += doctor.restart_check(getpass.getuser())
    briefs = ROOT / "logs" / "brief-errors.log"
    if briefs.exists():
        problems.append(("The last `butler brief` failed.", ASK_DOCTOR + " to have it explained"))
    if problems:
        say("I found something to fix:")
        for what, fix in problems:
            say(f"  {'' if plain_output() else '· '}{what} {fix}.")
        return 1
    say("Everything I can check without Claude looks healthy. For a deeper check, open Claude Code "
        "and type /butler-doctor.")
    return 0


def cmd_update(args) -> int:
    return updates.command(args.check, args.yes, say=say)


PORTAL_OPENER = ROOT / "data" / "portal-open.html"


def _listening(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
        return True
    except OSError:
        return False


def cmd_portal(args) -> int:
    """Opens the portal in this computer's browser and pairs that browser on the way. The one-time
    code travels in a file only this account can read, never on a command line, where other
    accounts on the computer could see it."""
    from . import devices
    from .config import load_settings
    port = load_settings()["portal"]["port"]
    who = _butler(ss.load())
    if not _listening(port):
        say(f"The portal isn't running. {ASK_DOCTOR}.")
        return 1
    code = devices.new_code()
    page = (f'<!doctype html><meta charset="utf-8"><title>Opening {html.escape(who)}</title>'
            f'<form method="post" action="http://127.0.0.1:{port}/pair">'
            f'<input type="hidden" name="code" value="{code}"><button>Open {html.escape(who)}</button></form>'
            "<script>document.forms[0].submit()</script>")
    PORTAL_OPENER.parent.mkdir(exist_ok=True)
    PORTAL_OPENER.unlink(missing_ok=True)
    fd = os.open(PORTAL_OPENER, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(page)
    if os.environ.get("BUTLER_NO_OPEN"):
        say(f"The portal is ready to open: {PORTAL_OPENER}")
        return 0
    system.open_file(str(PORTAL_OPENER))
    say(f"{who[0].upper() + who[1:]}'s portal is open in your browser, and this browser is paired. "
        "To add a phone, open Devices in the portal.")
    return 0


def commands() -> list[tuple[str, list[tuple[str, str]]]]:
    """Every command, grouped the way students meet them, each with a short plain description in
    this computer's words."""
    resume_by = "Butler in the Start menu" if system.NAME == "windows" else "the Butler app"
    return [
        ("Getting started", [
            ("butler setup", "the first run, or pick up where you left off"),
            ('butler name "Bento"', "change your butler's name"),
            ('butler town "Lisbon"', "where you live, for the weather and the time"),
            ("butler adults Sam Alex", "who the emails are for"),
            ("butler brief", "write a brief now and open it"),
            ("butler claude", "continue the course in Claude Code"),
            ("butler watch calendar", "open a module's video (a step, or a module number)"),
            ("butler claude-mode ask", "Claude Code asks before each change (or: auto)"),
            ("butler secret butler-anthropic", "store a key or password, typed hidden"),
        ]),
        ("Every day", [
            ("butler", "where you are in the course, and what's next"),
            ("butler resume", f"go straight to where you left off ({resume_by} does this)"),
            ("butler status", "what's set up, and what's next"),
            ("butler progress", "what your butler has told the course"),
            ("butler portal", f"open the portal in this {system.COMPUTERS[system.NAME]}'s browser"),
            ("butler update", "install a new version, if there is one"),
        ]),
        ("When something's wrong", [
            ("butler doctor", "check for problems"),
            ("butler help", "show this list"),
        ]),
    ]


def help_lines(width: int | None = None) -> list[str]:
    """The command list. Each command starts its own line, so it can be read and copied whole: its
    description beside it when the window is wide enough, else on the line below. A description
    that doesn't fit wraps under itself."""
    width = width or _width()
    col = max(len(command) for _, group in commands() for command, _ in group) + 2
    out = textwrap.wrap("The butler command. Each course lesson gives you one line to paste.", width)
    for title, group in commands():
        out += ["", f"{title}:"]
        for command, what in group:
            room = width - 2 - col
            if room >= 24:
                lines = textwrap.wrap(what, room, break_on_hyphens=False)
                out.append(f"  {command.ljust(col)}{lines[0]}")
                out += [" " * (2 + col) + line for line in lines[1:]]
            else:
                out.append(f"  {command}")
                out += [f"    {line}" for line in textwrap.wrap(what, max(width - 4, 12), break_on_hyphens=False)]
    return out


def print_help() -> None:
    for line in help_lines():
        print(line, flush=True)


class _Mistyped(Exception):
    """argparse would print its own error, in its own words; the student gets ours instead."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _Mistyped(message)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(prog="butler", add_help=False)
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("name")
    p.add_argument("name", nargs="*")
    p = sub.add_parser("town")
    p.add_argument("town", nargs="*")
    p = sub.add_parser("adults")
    p.add_argument("names", nargs="*")
    sub.add_parser("brief")
    sub.add_parser("status")
    sub.add_parser("resume")
    sub.add_parser("progress")
    p = sub.add_parser("setup")
    p.add_argument("--again", action="store_true")
    p = sub.add_parser("secret")
    p.add_argument("service", nargs="?")
    p = sub.add_parser("claude")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("watch")
    p.add_argument("what", nargs="?")
    p.add_argument("--again", action="store_true")
    p = sub.add_parser("claude-mode")
    p.add_argument("mode", nargs="?")
    sub.add_parser("doctor")
    sub.add_parser("portal")
    p = sub.add_parser("update")
    p.add_argument("--check", action="store_true")
    p.add_argument("--yes", action="store_true")
    sub.add_parser("help")
    handlers = {"name": cmd_name, "town": cmd_town, "adults": cmd_adults, "brief": cmd_brief,
                "status": cmd_status, "progress": cmd_progress, "claude": cmd_claude,
                "claude-mode": cmd_claude_mode, "doctor": cmd_doctor, "update": cmd_update,
                "portal": cmd_portal, "resume": cmd_resume, "setup": cmd_setup, "secret": cmd_secret,
                "watch": cmd_watch,
                None: cmd_home}
    words = sys.argv[1:] if argv is None else argv
    try:
        args = parser.parse_args(words)
    except _Mistyped:
        first = words[0] if words else ""
        if first in handlers or first == "help":
            say(f"That's not quite how butler {first} works. Here's the list, with an example of each:")
        else:
            say(f"I don't know \"butler {first}\". Check the spelling. Here's what I can do:")
        say()
        print_help()
        return 1
    if args.cmd == "help":
        print_help()
        return 0
    # Progress that couldn't be sent before (offline, or not signed in yet) goes now, and a step
    # finished since the last tick is noticed. Quietly: it never blocks or fails a command.
    logging.getLogger("butler").addHandler(logging.NullHandler())
    if args.cmd not in ("progress", "setup", "secret"):
        progress.sync(force=True)
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
