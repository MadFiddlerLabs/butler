"""The `butler` command. Every course lesson is one line that calls it:

    butler town "Lisbon"          butler adults Sam Alex          butler brief

`butler setup` (the guided first run) is plain bash in scripts/first-run.sh, so it starts before
Python exists. Everything here speaks plain words: the people typing these may never have used
a terminal before.
"""

import argparse
import logging
import os
import shutil
import re
import subprocess
import sys
import textwrap
from pathlib import Path

from . import progress, setup_settings as ss, updates
from .config import ROOT

CLAUDE_INSTALL = "curl -fsSL https://claude.ai/install.sh | bash"
# The first time, Claude Code opens on Module 1 (Meet Claude Code): a welcome, nothing changed.
# After that, /butler-setup on its own works out where the student is in the course.
FIRST_CLAUDE_PROMPT = (
    "Hi! I've just set up my butler and I'm starting Module 1 of the Build Your Butler course, "
    "Meet Claude Code. Please welcome me in plain words: say what you are, what you can do in this "
    "Butler folder, and that you'll always ask before changing anything. Don't change any files yet. "
    "Then suggest one simple first question I could ask you."
)
NEXT_CLAUDE_PROMPT = "/butler-setup"
CLAUDE_WELCOMED = ROOT / "data" / "claude-welcomed"


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
        say(f"I couldn't find \"{query}\". Try adding the country, like this: butler town \"{query}, Portugal\"")
        return 1
    s = ss.load()
    ss.set_town(s, place)
    ss.save(s)
    progress.report("setup.town")
    unit = "Fahrenheit" if s["weather"]["unit"] == "fahrenheit" else "Celsius"
    where = f"{place.name}, {place.country}" if place.country else place.name
    say(f"Got it: {where}. Your timezone is {place.timezone}, and weather comes in {unit}.")
    say(f'Not the right {place.name}? Add the country, like this: butler town "{place.name}, Portugal"')
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
    say(f"{who[0].upper() + who[1:]} is writing your brief. This takes a few seconds...")
    proc = subprocess.run([str(ROOT / "run.sh"), "preview"], cwd=ROOT, capture_output=True, text=True)
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
    local = Path.home() / ".local" / "bin" / "claude"
    return str(local) if local.exists() else None


def cmd_claude(args) -> int:
    dry = args.dry_run or bool(os.environ.get("BUTLER_CLAUDE_DRY_RUN"))
    exe = _claude_path()
    if not exe:
        say("Next, I move into Claude Code: an AI helper that does the rest of the setup with you.")
        say("It needs a paid Claude plan (Pro or Max) and a one-time sign-in in your browser.")
        if not args.yes:
            answer = input("Install Claude Code now? Press Enter for yes, or type no: ").strip().lower()
            if answer in {"n", "no"}:
                say("No problem. When you're ready, type: butler claude")
                return 0
        if dry:
            say(f"(dry run) would run: {CLAUDE_INSTALL}")
            exe = "claude"
        else:
            if subprocess.run(["bash", "-c", CLAUDE_INSTALL]).returncode != 0:
                say("The Claude Code installer didn't finish. Check your internet connection and run: butler claude")
                return 1
            exe = _claude_path()
            if not exe:
                say("Claude Code is installed, but I can't find it yet. Close this window, open Terminal again, "
                    "and type: butler claude")
                return 1
    progress.report("setup.claude", "started")
    first = not CLAUDE_WELCOMED.exists()
    prompt = FIRST_CLAUDE_PROMPT if first else NEXT_CLAUDE_PROMPT
    say("Opening Claude Code in your Butler folder. If it asks you to sign in, follow the link it shows.")
    say("It starts with Module 1: Meet Claude Code." if first else "It picks up where you left off in the course.")
    if dry:
        say(f"(dry run) would run: {exe} \"{prompt}\" in {ROOT}")
        return 0
    CLAUDE_WELCOMED.parent.mkdir(exist_ok=True)
    CLAUDE_WELCOMED.touch()
    os.chdir(ROOT)
    os.execv(exe, [exe, prompt])
    return 0  # not reached


# ---------- looking around ----------

def _checks() -> list[tuple[str, bool, str]]:
    """(what, done, the line that does it), in course order."""
    s = ss.load()
    named = s.get("name", ss.PLACEHOLDER_NAME) != ss.PLACEHOLDER_NAME
    services = {name: ss.has_secret(name) for name in
                ("butler-account", "butler-anthropic", "butler-gmail", "butler-lunchmoney")}
    scheduled = "com.buildyourbutler.butler" in subprocess.run(
        ["launchctl", "list"], capture_output=True, text=True).stdout if shutil.which("launchctl") else False
    return [
        ("Signed in to the course", services["butler-account"], "butler setup"),
        ("Your butler has a name", named, 'butler name "Bento"'),
        ("Your town and weather", bool(s.get("weather")), 'butler town "Lisbon"'),
        ("Who the emails are for", bool(s["household"].get("adults")), "butler adults Sam Alex"),
        ("Your first brief", ss.first_brief() is not None, "butler brief"),
        ("Claude Code", _claude_path() is not None, "butler claude"),
        ("Calendar and Reminders", (ROOT / "bin" / "butler-ek").exists() and bool(s["calendar"].get("include")),
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
        say(f"  {'✓' if done else '·'} {what}")
        if not done and next_step is None:
            next_step = how
    say()
    say(f"Next: {next_step}" if next_step else "Everything is set up. Well done.")
    return 0


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
        problems.append(("Python is too old for Butler.", "Run: scripts/bootstrap.sh"))
    try:
        s = ss.load()
        for key in ("timezone", "email", "household", "calendar", "news", "llm", "portal"):
            if key not in s:
                problems.append((f"Your settings are missing '{key}'.", "Run: butler town, or ask for help"))
    except Exception as e:
        problems.append((f"Your settings file can't be read ({str(e).splitlines()[0]}).",
                         "Restore config/settings.yaml from a backup, or ask for help"))
    log = ROOT / "logs" / "butler.log"
    if log.exists():
        errors = [l for l in log.read_text(errors="replace").splitlines()[-300:] if " ERROR " in l]
        if errors:
            problems.append((f"The log has {len(errors)} recent error(s).",
                             "Open Claude Code and type /butler-doctor to have them explained"))
    briefs = ROOT / "logs" / "brief-errors.log"
    if briefs.exists():
        problems.append(("The last `butler brief` failed.", "Its details are in logs/brief-errors.log"))
    if problems:
        say("I found something to fix:")
        for what, fix in problems:
            say(f"  · {what} {fix}.")
        return 1
    say("Everything I can check without Claude looks healthy. For a deeper check, open Claude Code "
        "and type /butler-doctor.")
    return 0


def cmd_update(args) -> int:
    return updates.command(args.check, args.yes, say=say)


HELP = """The butler command. Each course lesson gives you one line to paste.

  butler setup                  the guided first run
  butler name "Bento"           change your butler's name
  butler town "Lisbon"          where you live, for the weather and the time
  butler adults Sam Alex        who the emails are for
  butler brief                  write a brief now and open it
  butler status                 what's set up, and what's next
  butler progress               what your butler has told the course
  butler claude                 continue in Claude Code
  butler doctor                 check for problems
  butler update                 install a new version, if there is one
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="butler", add_help=False)
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("name")
    p.add_argument("name", nargs="*")
    p = sub.add_parser("town")
    p.add_argument("town", nargs="*")
    p = sub.add_parser("adults")
    p.add_argument("names", nargs="*")
    sub.add_parser("brief")
    sub.add_parser("status")
    sub.add_parser("progress")
    p = sub.add_parser("claude")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("doctor")
    p = sub.add_parser("update")
    p.add_argument("--check", action="store_true")
    p.add_argument("--yes", action="store_true")
    sub.add_parser("help")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        say(HELP)
        return 1
    handlers = {"name": cmd_name, "town": cmd_town, "adults": cmd_adults, "brief": cmd_brief,
                "status": cmd_status, "progress": cmd_progress, "claude": cmd_claude, "doctor": cmd_doctor,
                "update": cmd_update}
    if args.cmd not in handlers:
        say(HELP)
        return 0
    # Progress that couldn't be sent before (offline, or not signed in yet) goes now, and a step
    # finished since the last tick is noticed. Quietly: it never blocks or fails a command.
    logging.getLogger("butler").addHandler(logging.NullHandler())
    if args.cmd != "progress":
        progress.sync(force=True)
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
