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


class Terminal:
    """One window running `argv`. `type` sends a line (Enter at the end), `keys` raw keys, and
    `read` waits until the screen has been quiet for a moment, then returns what's new."""

    def __init__(self, argv: list[str], cwd: str, env: dict, columns: int = 120):
        self.screen = ""
        self.seen = 0
        self._lock = threading.Lock()
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
        return plain(new)

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
