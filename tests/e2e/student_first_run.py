"""The first run as a standard Mac user types it, in a real pseudo-terminal: the welcome, the name,
the Terms, the sign-in (skipped), the three pasted lessons, then `butler claude` for real: no dry
run and no stand-in, so Anthropic's installer (claude.ai/install.sh) runs, `claude` is found in the
same window, and Claude Code opens on its own first screen. Nobody signs in, so it costs nothing.

Run by the CI account, which starts the window as the student through sudo, the way Terminal
starts a login shell for them:

    python3 tests/e2e/student_first_run.py <user>

Everything the window showed goes to stdout, so a failure shows where it stopped. Stdlib only.
"""

import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import sys
import termios
import time

ESCAPES = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[=>()][0-9A-Za-z]?|\x1b[78]")
ROWS, COLUMNS = 40, 120

# Claude Code's first screens on a Mac where it has never run: the welcome and its text-style
# choice, then the sign-in choice. Any of them shows it started, found its terminal and waits for
# the student. Matched without spaces: Claude Code draws some lines with cursor moves.
CLAUDE_SCREENS = ("WelcometoClaudeCode", "Choosethetextstyle", "Selectloginmethod",
                  "Claudeaccountwithsubscription", "Let'sgetstarted")


def plain(text: str) -> str:
    return ESCAPES.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


class Window:
    """One Terminal window: a pty of ROWS x COLUMNS running argv. `expect` waits for text after
    what was matched last, the way a person reads down the window."""

    def __init__(self, argv: list[str]):
        pid, fd = pty.fork()
        if pid == 0:
            os.execvp(argv[0], argv)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLUMNS, 0, 0))
        self.pid, self.fd = pid, fd
        self.raw = ""
        self.cursor = 0
        self.done = False

    def _pump(self, timeout: float) -> None:
        if self.done:
            time.sleep(timeout)
            return
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return
        try:
            data = os.read(self.fd, 65536)
        except OSError:
            data = b""
        if not data:
            self.done = True
            return
        text = data.decode("utf-8", errors="replace")
        sys.stdout.write(plain(text))
        sys.stdout.flush()
        self.raw += text
        # A terminal answers the cursor-position question; Claude Code's screen library asks it.
        if "\x1b[6n" in text:
            os.write(self.fd, f"\x1b[{ROWS};1R".encode())

    def expect(self, *texts: str, limit: float = 120, squeeze: bool = False) -> str:
        """Waits for any of `texts` after the last match; returns the one found."""
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            seen = plain(self.raw)[self.cursor:]
            if squeeze:
                seen = re.sub(r"\s+", "", seen)
            for t in texts:
                at = seen.find(t)
                if at >= 0:
                    if not squeeze:
                        self.cursor += at + len(t)
                    return t
            if self.done:
                break
            self._pump(0.2)
        raise TimeoutError(f"the window never showed any of {texts!r} (after {limit:.0f}s"
                           f"{', and it closed' if self.done else ''})")

    def type(self, line: str) -> None:
        os.write(self.fd, (line + "\r").encode())

    def settle(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._pump(0.2)

    def close(self) -> None:
        for sig in (15, 9):
            try:
                os.kill(self.pid, sig)
            except ProcessLookupError:
                return
            time.sleep(1)


def fail(why: str) -> None:
    print(f"\nFAIL: {why}", flush=True)
    sys.exit(1)


def main() -> None:
    user = sys.argv[1]
    # sudo -i: the student's login shell, home and environment, as Terminal gives them. The
    # first run is what the installer opens in Terminal: scripts/first-run.command.
    # BUTLER_COLUMNS: no wrapping, so a sentence can be matched whole. BUTLER_NO_OPEN: the brief
    # isn't opened in a browser, since this account has no screen session on the runner.
    home = subprocess.run(["dscl", ".", "-read", f"/Users/{user}", "NFSHomeDirectory"],
                          capture_output=True, text=True, check=True).stdout.split(":", 1)[1].strip()
    command = f"{home}/Butler/scripts/first-run.command"
    w = Window(["sudo", "-u", user, "-i", "env", "TERM=xterm-256color", "LANG=en_US.UTF-8",
                "BUTLER_COLUMNS=1000", "BUTLER_NO_OPEN=1", command])
    try:
        w.expect("Hello! I'm Bento, your new butler.", limit=60)
        w.expect("Name ›")
        w.type("")                                   # keep the name from the package
        w.expect("By continuing, you agree to the Terms")
        w.expect("Press Enter to agree")
        w.type("")
        w.expect("Where did you hear about Butler?")
        w.expect("press Enter to skip ›")
        w.type("")
        w.expect("Your email ›")
        w.type("skip")                               # signing in needs an inbox: tests/test_first_run.py
        w.expect("Sign in later with: butler setup")
        w.expect("Bento is ready.", limit=900)       # Python, through the pinned uv
        w.expect("Paste here ›")
        w.type('butler town "Lisbon"')
        w.expect("Got it: Lisbon")
        w.expect("Is that right?")
        w.type("")
        w.expect("Paste here ›")
        w.type("butler adults Sam Alex")
        w.expect("I work for Sam and Alex.")
        w.expect("Paste here ›")
        w.type("butler brief")
        w.expect("Your brief is ready:", limit=300)
        w.expect("Paste here ›")
        w.type("butler claude")
        # Not installed yet for this account: the real installer, in this window.
        w.expect("Install Claude Code now?")
        w.type("")
        found = w.expect("Type 1 or 2", "I can't find it yet", "installer didn't finish", limit=600)
        if found != "Type 1 or 2":
            fail(f"after Anthropic's installer: {found}")
        w.type("")
        w.expect("Opening Claude Code in your Butler folder.")
        w.expect(*CLAUDE_SCREENS, limit=120, squeeze=True)
        w.settle(5)
    except TimeoutError as e:
        fail(str(e))
    finally:
        w.close()
    print("\nthe first run as the student, into Claude Code: all good", flush=True)


if __name__ == "__main__":
    main()
