"""A terminal window a persona types into, as a student's Terminal or PowerShell window is: a real
pseudo-terminal (pty on a Mac, ConPTY through pywinpty on Windows), so Butler's prompts, hidden
input and screen output behave as they do for a person. The persona sees plain text.
"""
import os
import re
import sys
import threading
import time

ESCAPES = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[=>()][0-9A-Za-z]?|\x1b[78]")


def plain(text: str) -> str:
    """Screen text without colours, cursor moves or carriage returns."""
    return ESCAPES.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


CONTROL = re.compile(r"\x1b\[([0-?]*)[ -/]*([@-~])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\\\)|\x1b[=>()][0-9A-Za-z]?|\x1b[78]|[\r\n\b]|[^\x1b\r\n\b]+")


class Screen:
    """The text a window shows, for output that redraws it. Windows' ConPTY repaints lines with
    cursor moves (PSReadLine redraws the command on every key), so stripping the escapes leaves
    each repaint in the text ("bbutbutl..."): round 3's Windows personas reported it as a bug.
    This applies the moves ConPTY uses (cursor position, up/down/left/right, column, erase line
    and screen) to a grid, and keeps the lines that scroll off. Colours and modes are ignored."""

    def __init__(self, rows: int = 40, columns: int = 120):
        self.rows, self.columns = rows, columns
        self.history: list[str] = []
        self.grid = [[] for _ in range(rows)]
        self.row = self.col = 0

    def _put(self, text: str) -> None:
        for ch in text:
            if self.col >= self.columns:
                self._newline()
                self.col = 0
            line = self.grid[self.row]
            if len(line) <= self.col:
                line.extend(" " * (self.col - len(line) + 1))
            line[self.col] = ch
            self.col += 1

    def _newline(self) -> None:
        if self.row == self.rows - 1:
            self.history.append("".join(self.grid.pop(0)).rstrip())
            self.grid.append([])
        else:
            self.row += 1

    def feed(self, data: str) -> None:
        for m in CONTROL.finditer(data):
            text = m[0]
            if text == "\r":
                self.col = 0
            elif text == "\n":
                self._newline()
            elif text == "\b":
                self.col = max(0, self.col - 1)
            elif m[2]:
                args = [int(a) if a.isdigit() else 0 for a in m[1].lstrip("?").split(";")] if m[1] else []
                n = args[0] if args and args[0] else 1
                if m[2] == "H" or m[2] == "f":
                    self.row = min(max((args[0] if args else 1) or 1, 1), self.rows) - 1
                    self.col = min(max((args[1] if len(args) > 1 else 1) or 1, 1), self.columns) - 1
                elif m[2] == "A":
                    self.row = max(0, self.row - n)
                elif m[2] == "B":
                    self.row = min(self.rows - 1, self.row + n)
                elif m[2] == "C":
                    self.col = min(self.columns - 1, self.col + n)
                elif m[2] == "D":
                    self.col = max(0, self.col - n)
                elif m[2] == "G":
                    self.col = min(n, self.columns) - 1
                elif m[2] == "K":
                    mode = args[0] if args else 0
                    line = self.grid[self.row]
                    if mode == 0:
                        del line[self.col:]
                    elif mode == 1:
                        line[:self.col + 1] = [" "] * min(len(line), self.col + 1)
                    else:
                        line.clear()
                elif m[2] == "J":
                    mode = args[0] if args else 0
                    if mode == 0:
                        del self.grid[self.row][self.col:]
                        for r in range(self.row + 1, self.rows):
                            self.grid[r] = []
                    elif mode in (2, 3):
                        self.history += [ln for ln in ("".join(r).rstrip() for r in self.grid) if ln]
                        self.grid = [[] for _ in range(self.rows)]
            elif not text.startswith("\x1b"):
                self._put(text)

    def lines(self) -> list[str]:
        shown = ["".join(r).rstrip() for r in self.grid]
        while shown and not shown[-1]:
            shown.pop()
        return self.history + shown


class Terminal:
    """One window running `argv`. `type` sends a line (Enter at the end), `keys` raw keys, and
    `read` waits until the screen has been quiet for a moment, then returns what's new."""

    def __init__(self, argv: list[str], cwd: str, env: dict, columns: int = 120):
        self.screen = ""
        self.seen = 0
        self._lock = threading.Lock()
        # On Windows the persona reads what the window shows (Screen), not the raw stream.
        self._view = Screen(40, columns) if sys.platform == "win32" else None
        self._shown: list[str] = []
        if sys.platform == "win32":
            from winpty import PtyProcess
            self._proc = PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(40, columns))
            self._write = self._proc.write
            self._alive = self._proc.isalive
            reader = self._proc.read
        else:
            import pty
            import subprocess
            master, slave = pty.openpty()
            os.set_blocking(master, True)
            self._proc = subprocess.Popen(argv, cwd=cwd, env={**env, "COLUMNS": str(columns), "LINES": "40"},
                                          stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
            os.close(slave)
            self._fd = master
            self._write = lambda s: os.write(master, s.encode())
            self._alive = lambda: self._proc.poll() is None

            def reader(_size):
                try:
                    return os.read(master, 4096).decode("utf-8", errors="replace")
                except OSError:
                    raise EOFError from None
        threading.Thread(target=self._pump, args=(reader,), daemon=True).start()

    def _pump(self, reader) -> None:
        while True:
            try:
                data = reader(4096)
            except (EOFError, OSError):
                return
            if not data:
                if not self._alive():
                    return
                time.sleep(0.05)
                continue
            with self._lock:
                self.screen += data
                if self._view:
                    self._view.feed(data)

    def type(self, line: str) -> None:
        self.keys(line + "\r")

    def keys(self, text: str) -> None:
        for chunk in (text[i:i + 64] for i in range(0, len(text), 64)):
            self._write(chunk)
            time.sleep(0.02)

    def read(self, quiet: float = 2.0, limit: float = 120.0) -> str:
        """What appeared since the last read, once nothing new has come for `quiet` seconds (or
        `limit` passes: a long download keeps printing)."""
        start = time.monotonic()
        last_len, last_change = -1, time.monotonic()
        while time.monotonic() - start < limit:
            with self._lock:
                now = len(self.screen)
            if now != last_len:
                last_len, last_change = now, time.monotonic()
            elif time.monotonic() - last_change >= quiet:
                break
            if not self._alive() and time.monotonic() - last_change >= 0.5:
                break
            time.sleep(0.1)
        with self._lock:
            new, self.seen = self.screen[self.seen:], len(self.screen)
            lines = self._view.lines() if self._view else None
        if lines is None:
            return plain(new)
        return self.since(lines)

    def since(self, lines: list[str]) -> str:
        """The shown lines from the first one that changed since the last read: a line being
        typed on comes back whole, once."""
        same = 0
        while same < min(len(lines), len(self._shown)) and lines[same] == self._shown[same]:
            same += 1
        self._shown = lines
        return "\n".join(lines[same:]) + ("\n" if len(lines) > same else "")

    def wait_new(self, limit: float = 30.0) -> bool:
        """Waits until something new appears in the window, as a person watching it would."""
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            with self._lock:
                if len(self.screen) > self.seen:
                    return True
            if not self._alive():
                return False
            time.sleep(0.2)
        return False

    def wait_for(self, text: str, limit: float = 300.0) -> bool:
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            with self._lock:
                if text in plain(self.screen[self.seen:]):
                    return True
            if not self._alive():
                return False
            time.sleep(0.2)
        return False

    @property
    def open(self) -> bool:
        return self._alive()

    def close(self) -> None:
        if not self._alive():
            return
        if sys.platform == "win32":
            self._proc.terminate(force=True)
        else:
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except Exception:  # noqa: BLE001 - a window that won't close is killed
                self._proc.kill()
