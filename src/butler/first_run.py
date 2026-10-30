"""The guided first run in Python: scripts/first-run.sh's twin, for Windows, where there is no
bash. `butler setup` runs it there (bin/butler.cmd, then butler.cli), and so does `run.py setup`.
On a Mac, first-run.sh stays the first run, because it has to start before Python exists.

Same prompts, in the same order, writing the same files, with the same exit codes. The words
differ only where the computer does: "this computer" for "this Mac", Credential Manager for
Keychain, the PATH for ~/.zprofile. tests/test_first_run_python.py drives both with the same
answers against the fake account API and compares what they print, write and send, so a change
to one needs the same change in the other.

    run.py setup [--again]     --again: installed over an existing Butler, resume or start fresh

Test hooks, as in first-run.sh: BUTLER_API, BUTLER_BOOTSTRAP (a command that sets up Python),
BUTLER_POLL_SECONDS, BUTLER_POLL_LIMIT, BUTLER_COLUMNS.
"""

import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import account, course_video, system
from . import setup_settings as ss
from .config import ROOT, plain_output, store_secret

# The ten answers to "Where did you hear about Butler?", as scripts/lib/heard.sh lists them, in
# account.HEARD_SOURCES' order. Only the token is ever sent, never the student's own words.
HEARD_LABELS = ("A friend or family member", "A web search", "Reddit", "Hacker News", "Product Hunt",
                "YouTube", "Social media", "A newsletter", "A podcast", "Somewhere else")
EMAIL = re.compile(r'[^@"\\]+@[^@"\\]+\.[^@"\\]+')
COMPUTER = system.COMPUTERS
# What this window is, said first: most students have never used a terminal (persona round, 30 Oct 2026).
WINDOW_LINE = "This window is {terminal}: you type short lines here, and I tell you each one. Closing it does no harm: "
COME_BACK = {
    "app": "the Butler app brings you back.",
    "resume": "type butler resume in a new Terminal window to come back.",
    "windows": "click Start, type Butler and press Enter to come back.",
}
PATH_NOTE = {
    "mac": 'I added one line to ~/.zprofile so you can type "butler" in any new Terminal window.',
    "windows": 'I added Butler to your PATH so you can type "butler" in any new Terminal window.',
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(path: Path, text: str) -> None:
    """UTF-8 with \\n line ends on every computer, as bash writes them. Whole or not at all: a
    window closed mid-write never leaves an empty file (#37)."""
    new = path.with_name(path.name + ".new")
    new.write_text(text, encoding="utf-8", newline="\n")
    os.replace(new, path)


def heard_token(answer: str) -> str | None:
    """The token for a number from 1 to 10, or None (scripts/lib/heard.sh's heard_token)."""
    n = re.sub(r"[\s.]", "", answer)
    return account.HEARD_SOURCES[int(n) - 1] if re.fullmatch(r"[1-9]|10", n) else None


def venv_python(root: Path = ROOT) -> Path:
    """Butler's own Python, which the bootstrap (or the installer) sets up."""
    if system.NAME == "windows":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


class Keyboard:
    """The student's answers, one line at a time. From a pipe it reads a byte at a time, as bash's
    `read` does, so a `butler` command started in between reads the next line itself."""

    def __init__(self):
        self._pending: queue.Queue | None = None

    @staticmethod
    def _read() -> str | None:
        """One line, or None when the input has ended."""
        if sys.stdin is not None and sys.stdin.isatty():
            line = sys.stdin.readline()
            return line if line.endswith("\n") else None
        data = b""
        while True:
            byte = os.read(0, 1)
            if not byte:
                return None
            if byte == b"\n":
                return data.decode("utf-8", errors="replace")
            data += byte

    def line(self, prompt: str) -> str | None:
        """Shows the prompt and returns the answer without the spaces around it, as `read -r`
        does, or None when the input has ended."""
        print(prompt, end="", flush=True)
        if self._pending is not None:
            got, self._pending = self._pending.get(), None
        else:
            got = self._read()
        return None if got is None else got.strip(" \t\r\n")

    def within(self, seconds: float) -> tuple[bool, str | None]:
        """A line typed within `seconds`, as `read -t`: (True, the line, or None when the input
        has ended) or (False, None) when nothing came. The read goes on in the background, and
        the next `line` takes its answer."""
        if self._pending is None:
            pending: queue.Queue = queue.Queue(maxsize=1)
            threading.Thread(target=lambda: pending.put(self._read()), daemon=True).start()
            self._pending = pending
        try:
            got = self._pending.get(timeout=seconds)
        except queue.Empty:
            return False, None
        self._pending = None
        return True, None if got is None else got.strip(" \t\r\n")


class FirstRun:
    def __init__(self, again: bool = False, root: Path = ROOT, keyboard: Keyboard | None = None):
        self.root = root
        self.again = again
        self.keys = keyboard or Keyboard()
        self.data = root / "data"
        self.log = root / "logs" / "setup.log"
        self.version = self._read(root / ".butler-version") or "dev"
        self.poll_seconds = float(os.environ.get("BUTLER_POLL_SECONDS") or 3)
        self.poll_limit = int(os.environ.get("BUTLER_POLL_LIMIT") or 300)   # 15 minutes, the link's life
        self.computer = system.COMPUTERS[system.NAME]
        self.plain = plain_output()
        colour = sys.stdout.isatty() and not self.plain and system.NAME != "windows"
        self.bold, self.dim, self.green, self.reset = (
            ("\033[1m", "\033[2m", "\033[32m", "\033[0m") if colour else ("", "", "", ""))
        self.dot = ": " if self.plain else " \u00b7 "      # a screen reader skips a middle dot or names it
        # Prose wraps to the window, at most 80 columns. BUTLER_COLUMNS overrides it (tests use it).
        columns = os.environ.get("BUTLER_COLUMNS", "")
        if not columns.isdigit():
            columns = str(min(shutil.get_terminal_size((80, 24)).columns, 80))
        self.width = int(columns) - 4
        self.bootstrap: subprocess.Popen | None = None
        self.name = ""
        self.fresh = False       # started fresh, after --again

    # ---------- what the student sees ----------

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").replace("\r", "").replace("\n", "")
        except OSError:
            return ""

    def wrap(self, text: str, width: int) -> list[str]:
        """Never mid-word, and `butler <command>` in a sentence stays on one line, so it can be
        copied whole."""
        glued = re.sub(r"butler (?=[a-z])", "butler\x1f", text)
        lines = textwrap.wrap(glued, max(width, 20), break_on_hyphens=False) or [""]
        return [line.replace("\x1f", " ") for line in lines]

    def say(self, text: str = "") -> None:
        for line in self.wrap(text, self.width):
            print(line, flush=True)

    def ok(self, text: str) -> None:
        """A tick for the eye. The sentence says it worked, so plain output leaves the tick out."""
        if self.plain:
            return self.say(text)
        first, *rest = self.wrap(text, self.width - 2)
        print(f"{self.green}\u2713{self.reset} {first}", flush=True)
        for line in rest:
            print(f"  {line}", flush=True)

    def heading(self, text: str) -> None:
        print(f"\n{self.bold}{text}{self.reset}", flush=True)

    def lesson_line(self, text: str) -> None:
        print(f"\n    {self.bold}{text}{self.reset}\n", flush=True)

    @staticmethod
    def clear() -> None:
        if sys.stdout.isatty():
            os.system("cls" if system.NAME == "windows" else "clear")

    def note(self, line: str) -> None:
        """One line in logs/setup.log."""
        with open(self.log, "a", encoding="utf-8", newline="\n") as f:
            f.write(f"{_now()} {line}\n")

    # ---------- the butler command and Python ----------

    def butler(self, *args: str, to_log: bool = False) -> int:
        """`butler <args>`, as the student would type it: bin/butler on a Mac, run.py on Windows."""
        if system.NAME == "windows":
            argv = [str(venv_python(self.root)), "-X", "utf8", str(self.root / "run.py"), "cli", *args]
        else:
            argv = [str(self.root / "bin" / "butler"), *args]
        sys.stdout.flush()
        if not to_log:
            return subprocess.run(argv).returncode
        with open(self.log, "ab") as log:
            return subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT).returncode

    def video(self) -> None:
        """Module 0's video, before the first question, once (course_video.py), while Python
        installs. Enter carries on; with no keyboard (a script), it carries on by itself."""
        if 0 in course_video.opened(self.data):
            return
        self.say()
        for line in course_video.open_page(0, self.data):
            self.say(line)
        if sys.stdin is not None and sys.stdin.isatty():
            self.keys.line(course_video.READY)

    def start_bootstrap(self) -> None:
        """Python installs while the student checks their email. On Windows this first run already
        runs in Butler's Python (scripts/bootstrap.ps1 set it up first), so there's nothing to
        start unless BUTLER_BOOTSTRAP says what."""
        command = os.environ.get("BUTLER_BOOTSTRAP")
        if not command and system.NAME != "windows":
            command = str(self.root / "scripts" / "bootstrap.sh")
        if not command:
            return
        argv = command if system.NAME == "windows" else ["bash", "-c", command]
        with open(self.log, "ab") as log:
            self.bootstrap = subprocess.Popen(argv, shell=system.NAME == "windows", cwd=self.root,
                                              stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)

    def python_ready(self) -> bool:
        if self.bootstrap is not None and self.bootstrap.wait() != 0:
            return False
        return os.access(venv_python(self.root), os.X_OK)

    # ---------- welcome back ----------

    def start_fresh(self) -> bool:
        """Moves the household's files to backups/start-fresh-<date>/, after a clear yes. Deletes
        nothing. The Terms and the "where did you hear" answer stay: they're the student's, not the
        household's. So do the password store and the logs. False when the student changes their
        mind."""
        candidates = sorted(self.root.glob("config/*.yaml")) + sorted(self.data.glob("*"))
        candidates += [self.root / p for p in ("out", "projects", "budget/budget.xlsx", "budget/history")]
        items = [p.relative_to(self.root).as_posix() for p in candidates
                 if p.exists() and not p.name.startswith(".") and not p.name.endswith(".example.yaml")
                 and p not in (self.data / "terms.json", self.data / "heard.json")]
        if not items:
            self.say("There's nothing of yours to move yet, so this is already a fresh start.")
            return True
        backup = f"backups/start-fresh-{datetime.now():%Y-%m-%d-%H%M%S}"
        self.say(f"Starting fresh moves your settings and your butler's records into a backup folder, {backup}, "
                 f"inside your Butler folder. Nothing is deleted. Your passwords stay in {system.PASSWORD_STORE}.")
        confirm = (self.keys.line("Type yes to start fresh, or press Enter to resume instead \u203a ") or "").lower()
        if confirm not in ("y", "yes"):
            self.say("Alright, nothing moved. Let's pick up where you left off.")
            return False
        for item in items:
            target = self.root / backup / item
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(self.root / item), str(target))
        self.note(f"start fresh: moved {' '.join(items)} to {backup}")
        self.ok(f"Done. Your old settings are safe in {backup}. Let's begin again.")
        return True

    def welcome_back(self) -> bool:
        """Resume (the default) or start fresh. True when the run is over: resumed after a
        finished first run, `butler` on its own has said where the student is."""
        self.note("welcome back")
        if not self.plain:
            self.clear()
        self.heading("Welcome back.")
        self.say(f"Butler was already on this {self.computer}. The new version is in place, and nothing of yours "
                 "has changed.")
        self.say("1. Resume where you left off.")
        self.say("2. Start fresh. Your old settings go into a backup folder first, and nothing is deleted.")
        while True:
            choice = (self.keys.line("Press Enter to resume, or type 2 to start fresh \u203a ") or "").lower()
            if choice in ("", "1", "resume"):
                break
            if choice in ("2", "fresh", "start fresh"):
                self.fresh = self.start_fresh()
                break
            self.say("Type 1 or 2, or press Enter to resume.")
        if not self.fresh and (self.data / "first-run-done").exists() and os.access(venv_python(self.root), os.X_OK):
            self.butler()        # where you are in the course, the one thing to do next, the commands
            return True
        return False

    # ---------- the account ----------

    def signed_in(self) -> tuple[bool, str]:
        """Signed in already (a run that stopped, or a reinstall): no need to ask the name or email
        again. The server says whether the session still works; offline, a stored one counts."""
        token = account.session()
        if not token:
            return False, ""
        try:
            return True, str(account.whoami(token).get("email") or "")
        except account.AccountError as e:
            return e.status not in (401, 403), ""

    def terms(self) -> bool:
        """The Terms, once per version: data/terms.json records the agreement, and it goes to the
        server with the sign-in (now or, offline, later). False when the student says no."""
        version = self._read(self.root / "TERMS_VERSION") or "unknown"
        terms_file = self.data / "terms.json"
        try:
            agreed = json.loads(terms_file.read_text(encoding="utf-8")).get("terms_version")
        except (OSError, ValueError, AttributeError):
            agreed = None
        if agreed == version:
            return True
        self.say("By continuing, you agree to the Terms (https://buildyourbutler.com/terms/) and Privacy "
                 "(https://buildyourbutler.com/privacy/).")
        agree = (self.keys.line("Press Enter to agree, or type no to stop \u203a ") or "").lower()
        if agree in ("n", "no"):
            return False
        _write(terms_file, json.dumps({"terms_version": version, "terms_accepted_at": _now()},
                                      separators=(",", ":"), ensure_ascii=False) + "\n")
        return True

    def heard(self) -> None:
        """Where the student heard about Butler, asked once: data/heard.json keeps the answer, or
        the skip. account.start sends it with the sign-in, now or later."""
        heard_file = self.data / "heard.json"
        if heard_file.exists():
            return
        self.say("Where did you hear about Butler? (optional)")
        for i, label in enumerate(HEARD_LABELS, 1):
            print(f"  {i:2d}. {label}", flush=True)
        token = None
        while True:
            answer = self.keys.line("Type a number, or press Enter to skip \u203a ") or ""
            if not answer.strip():
                break
            token = heard_token(answer)
            if token:
                break
            self.say(f"Type a number from 1 to {len(HEARD_LABELS)}, or press Enter to skip.")
        _write(heard_file, json.dumps({"heard": token}, separators=(",", ":")) + "\n")

    def sign_in(self) -> None:
        self.heard()
        self.say("I'll email you a link to sign in. It keeps your place in the course.")
        self.say(f"We never see your household's data. It goes only from this {self.computer} to services you "
                 "choose, such as Anthropic and Google.")
        next_email = typed = None
        while True:
            # A retyped address while waiting is said back as it's sent; one typed here is checked first.
            confirm = not next_email
            if next_email:
                email, next_email = next_email, None
            elif typed:
                email, typed = typed, None
            else:
                email = self.keys.line("Your email \u203a ") or ""
            email = re.sub(r"\s", "", email)
            if not email or email == "skip":
                self.say("No problem. Sign in later with: butler setup")
                return
            if not EMAIL.fullmatch(email):
                self.say("That doesn't look like an email address. Try again, or type skip.")
                continue
            if confirm:
                answer = re.sub(r"\s", "", self.keys.line(f"Send the link to {email}? Press Enter, or type the right "
                                                           "address \u203a ") or "")
                if answer.lower() not in ("", "y", "yes"):
                    typed = answer
                    continue
            try:
                started = account.start(email, self.name, f"setup/{self.version}")
            except account.AccountError as e:
                self.note(f"sign-in: {e}")
                if e.status is None:
                    self.say("I can't reach buildyourbutler.com right now. Let's carry on, and sign in later with: "
                             "butler setup")
                    return
                if e.status >= 500:      # our side (email sign-in down or not open yet), not the student's
                    self.say("Sign-in isn't available right now. That's on our side, not yours. Let's carry on, "
                             "and sign in later with: butler setup")
                    return
                self.say(f"That didn't work ({e}). Try again, or type skip.")
                continue
            # The address back, so a typo shows; while it waits, a corrected one sends a new link.
            self.ok(f"Sent to {email}! Check your inbox for an email from Butler and click the link inside.")
            self.say("Waiting for your click. I'm getting my things ready meanwhile. Wrong address? Type the right "
                     "one. To do this later, type skip and press Enter.")
            token, status = self.wait_for_click(str(started.get("request_id", "")), str(started.get("poll_secret", "")))
            if token:
                self.signed_in_as(email, token)
                return
            if status.startswith("retype:"):
                next_email = status.removeprefix("retype:")
                continue
            if status == "expired":
                continue
            self.say("We'll finish signing in later. Type: butler setup")
            return

    def wait_for_click(self, request_id: str, poll_secret: str) -> tuple[str | None, str]:
        """Polls until the link is clicked, it expires, or the student types skip."""
        status = ""
        for _ in range(self.poll_limit):
            try:
                answer = account.poll(request_id, poll_secret)
            except account.AccountError:
                answer = {}
            status = str(answer.get("status") or "")
            if status == "verified":
                return answer.get("session_token") or None, status
            if status == "expired":
                self.say("That link expired. Let's send a new one.")
                return None, status
            typed, line = self.keys.within(self.poll_seconds)
            line = re.sub(r"\s", "", line or "")
            if typed and line == "skip":
                break
            if typed and EMAIL.fullmatch(line):
                self.say(f"Sending a new link to {line}.")
                return None, f"retype:{line}"
            if typed and line:
                self.say("Type your email again if it was wrong, or type skip to do this later.")
        return None, status

    def signed_in_as(self, email: str, token: str) -> None:
        try:
            store_secret(account.KEYCHAIN_ACCOUNT, token)
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            self.note(f"sign-in: couldn't save the session ({type(e).__name__})")
            self.say(f"I couldn't save your sign-in to {system.PASSWORD_STORE}. You can sign in again later with: "
                     "butler setup")
        try:
            account.send_progress(token, "setup.email", "done")
        except account.AccountError:
            pass
        self.ok(f"You're signed in as {email}.")
        self.say("Would you like one short email a week with your next lesson? You can stop any time.")
        answer = self.keys.line("Weekly lesson email? Type yes, or press Enter for no \u203a ") or ""
        nudges = re.sub(r"\s", "", answer.lower()) in ("y", "yes")
        try:
            account.set_nudges(token, nudges)
        except account.AccountError:
            pass
        if nudges:
            self.ok("Done. One email a week, and every one has a stop link.")
        else:
            self.say("No weekly emails. Change your mind any time on buildyourbutler.com.")

    # ---------- the name ----------

    def welcome(self, signed_in: bool, resuming: bool) -> None:
        """The butler, by the name chosen on the website (.butler-name), or a name asked for."""
        name = self._read(self.root / ".butler-name")
        name = ss.valid_name(name) if name != ss.PLACEHOLDER_NAME else None
        if not self.plain and not self.again:
            self.clear()         # a screen reader may want what came before
        if name and signed_in:
            self.heading(f"Hello again! It's {name}, your butler.")
        elif name:
            self.heading(f"Hello! I'm {name}, your new butler.")
            self.say("Is that the name you'd like? Press Enter to keep it, or type a new one.")
            answer = self.keys.line("Name \u203a ") or ""
            if answer:
                if ss.valid_name(answer):
                    name = ss.valid_name(answer)
                else:
                    self.say(f'I\'ll keep {name} for now. You can change it later, for example: butler name "Jeeves"')
        else:
            self.heading("Hello! I'm your new butler.")
            while not name:
                self.say("What would you like to call me? Type a name: letters, numbers, spaces or hyphens, "
                         "up to 24 characters.")
                answer = self.keys.line("Name \u203a ")
                name = ss.valid_name(ss.PLACEHOLDER_NAME if answer is None else answer)
                if not name:
                    self.say("That one won't work. Try another.")
        self.name = name
        _write(self.root / ".butler-name", name + "\n")
        self.say()
        windows = system.NAME == "windows"
        come_back = "windows" if windows else "app" if system.butler_app() else "resume"
        self.say(WINDOW_LINE.format(terminal="a Terminal" if windows else "Terminal") + COME_BACK[come_back])
        if resuming:
            self.say("Let's pick up where we left off. I'll skip whatever is done already.")
        else:
            self.say("Let's get me settled in. It takes about ten minutes, two of them yours.")

    # ---------- the first lessons ----------

    def run_line(self, want: str, line: str) -> int:
        """Runs a pasted `butler <want> ...` line: 0 when it worked, 1 when it didn't, 2 when the
        line isn't that command. Splits like a shell would, quotes included, but never runs
        anything but `butler <want>`. A quote left open ends the line there, as xargs does in
        first-run.sh."""
        lexer = shlex.shlex(line, posix=True)
        lexer.whitespace_split = True
        parts = []
        try:
            parts.extend(lexer)
        except ValueError:
            pass
        if len(parts) < 2 or parts[0] != "butler" or parts[1] != want:
            return 2
        return 0 if self.butler(*parts[1:]) == 0 else 1

    def lesson(self, want: str, example: str, optional: bool = False) -> bool:
        """Reads a pasted `butler <want> ...` line and runs it. False when the input ends, or, for
        an optional lesson, when the student presses Enter or types later."""
        self.lesson_line(example)
        while True:
            line = self.keys.line("Paste here \u203a ")
            if line is None:
                return False
            line = line.strip()
            if optional and line in ("", "later"):
                return False
            result = self.run_line(want, line)
            if result == 0:
                return True
            if result == 2:
                self.say(f"Paste the lesson's line, starting with: butler {want}")
            else:
                self.say("Let's try that again.")

    def town_lesson(self) -> bool:
        """The town, then a check: two towns can share a name, and the lesson says to paste it again."""
        if not self.lesson("town", 'butler town "Lisbon"'):
            return False
        while True:
            line = self.keys.line("Is that right? Press Enter, or paste the line again \u203a ")
            if line is None or not line.strip():
                return True
            if self.run_line("town", line.strip()) == 2:
                self.say("To change the town, paste a line starting with: butler town. If it's right, press Enter.")

    def lessons(self) -> bool:
        """Town, adults and the first brief; one done already (by an earlier run or by hand) is
        skipped. False when the input ended halfway."""
        done = ss.done_steps(self.root)
        self.heading(f"Step 2 of 4{self.dot}Where you live")
        if "town" in done:
            self.ok("Already done: I know where you live.")
        else:
            self.say("Every lesson is one line you copy and paste. Here's your first. Paste it at the \u203a prompt, "
                     "change the town to yours, and press Enter.")
            if not self.town_lesson():
                return False
        self.heading(f"Step 3 of 4{self.dot}Who I work for")
        if "adults" in done:
            self.ok("Already done: I know who I work for.")
        else:
            self.say("Now the adults my emails are for. First names only.")
            if not self.lesson("adults", "butler adults Sam Alex"):
                return False
        self.heading(f"Step 4 of 4{self.dot}Your first brief")
        if "brief" in done:
            self.ok("Already done: your first brief is written.")
        else:
            self.say("Now the fun part. Paste this, and I'll write you a brief right now.")
            if not self.lesson("brief", "butler brief"):
                return False
        return True

    # ---------- the whole run ----------

    def run(self) -> int:
        for folder in ("logs", "data", "out"):
            (self.root / folder).mkdir(exist_ok=True)
        resuming = (self.data / "first-run-started").exists()
        # The `butler` command, from any new Terminal window. First, before anything can stop the
        # setup, because every way out says "type: butler setup".
        path_added = system.add_to_path(str(self.root / "bin"))

        if self.again and self.welcome_back():
            return 0
        if self.fresh:
            resuming = False
        if not (self.data / "first-run-started").exists():
            _write(self.data / "first-run-started", _now() + "\n")

        signed_in, signed_email = self.signed_in()
        self.welcome(signed_in, resuming)
        self.start_bootstrap()
        self.video()

        self.heading(f"Step 1 of 4{self.dot}Your email")
        if not self.terms():
            if self.bootstrap is not None:
                self.bootstrap.terminate()
            self.say("No problem. Nothing has been sent anywhere. If you change your mind, open a new Terminal "
                     "window and type: butler setup")
            return 0
        if signed_in:
            self.ok(f"Already done: you're signed in{' as ' + signed_email if signed_email else ''}.")
        else:
            self.sign_in()

        # One line while it works, one when it's done: a ticker of dots is noise to a screen reader.
        if self.bootstrap is not None and self.bootstrap.poll() is None:
            self.say(f"Getting {self.name} ready. This can take a few minutes.")
        if not self.python_ready():
            self.say("Something went wrong getting ready. Check your internet connection, then open a new Terminal "
                     "window and type: butler setup")
            self.say("If it keeps happening, we can help: https://buildyourbutler.com/support/")
            return 1
        self.ok(f"{self.name} is ready.")
        if path_added:
            self.say(f"{self.dim}{PATH_NOTE.get(system.NAME, PATH_NOTE['windows'])}{self.reset}")
        self.butler("name", self.name, to_log=True)

        if not self.lessons():
            return 1
        # The first run is over. The installer reads this: installing again offers resume or start fresh.
        _write(self.data / "first-run-done", _now() + "\n")

        # The Butler app is the way back (on Windows, Butler in the Start menu), as the lessons say.
        # Say where it is, once. The Dock stays the student's to change.
        come_back, goodbye = way_back(system.butler_app())
        if come_back:
            self.say()
            self.say(come_back)

        self.heading("What's next")
        self.say(f"That's your butler, running on your {self.computer}. Next, I move into Claude Code, an AI helper "
                 "that teaches me your calendar, your email and more, one lesson at a time. Paste this when you're "
                 "ready, or press Enter to stop here for today.")
        if self.lesson("claude", "butler claude", optional=True):
            return 0
        self.say()
        self.say(goodbye)
        return 0


def way_back(app: str | None) -> tuple[str | None, str]:
    """(where the way back is, said once at the end of the first run; the goodbye). The lessons
    say the same: the Butler app on a Mac, Butler in the Start menu on Windows, or `butler resume`."""
    if system.NAME == "windows":
        return ("To come back any time, click Start, type Butler and press Enter.",
                "See you soon. When you're ready, click Start, type Butler and press Enter, or type butler resume in a "
                "new Terminal window.")
    if not app:
        return None, "See you soon. When you're ready, type butler resume in a new Terminal window."
    goodbye = "See you soon. When you're ready, open the Butler app, or type butler resume in a new Terminal window."
    if Path(app).is_relative_to(Path.home()):
        return ("To come back any time, open the Butler app: it's in the Applications folder inside your home folder, "
                "and Spotlight finds it too. Drag it to the Dock to keep it one click away."), goodbye
    return "To come back any time, open the Butler app in your Applications folder. Drag it to the Dock to keep it one click away.", goodbye


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        return FirstRun(again=args[:1] == ["--again"]).run()
    except KeyboardInterrupt:
        print(flush=True)
        return 130


if __name__ == "__main__":
    sys.exit(main())
