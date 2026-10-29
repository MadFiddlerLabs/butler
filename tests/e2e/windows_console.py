"""The installed Butler in a real Windows console, as a household meets it (#40). Run by
windows-e2e.yml after the installer, with a Python that has pywinpty; not part of the suite.

Everything else in CI runs Butler with pipes. A household types into a console, where Python
reads keys differently (getpass reads them one at a time, unechoed) and a prompt's "›" has to
survive the console's code page. Through a pseudo-console (ConPTY, what Windows Terminal uses),
this checks:

  - A new terminal finds `butler`: a cmd.exe and a PowerShell started with the environment
    Windows gives a new window (the system and user PATH from the registry, expanded), not this
    process's, run plain `butler help`. PATHEXT turns `butler` into bin\\butler.cmd.
  - `butler secret butler-anthropic` takes a key typed hidden: it never shows on screen, and it
    lands in Credential Manager. The key is an invented test value.
  - `butler setup` asks its questions in the console and takes the answers: Enter keeps the
    name from the installer, and "no" to the Terms stops it politely, with nothing sent.

  python tests/e2e/windows_console.py "%USERPROFILE%\\Butler"
"""
import ctypes
import os
import re
import subprocess
import sys
import threading
import time
import winreg
from pathlib import Path

from winpty import PtyProcess

TEST_KEY = "sk-ant-console-test-0000"
ESCAPES = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[=>()][0-9A-Za-z]?")


def new_window_environment() -> dict:
    """The environment Explorer gives a window opened now: this process's, with the PATH rebuilt
    from the registry (system, then user), as it is after WM_SETTINGCHANGE."""
    def read(root, key):
        try:
            with winreg.OpenKey(root, key) as k:
                return str(winreg.QueryValueEx(k, "Path")[0])
        except FileNotFoundError:
            return ""
    machine = read(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
    user = read(winreg.HKEY_CURRENT_USER, "Environment")
    buffer = ctypes.create_unicode_buffer(32767)
    ctypes.windll.kernel32.ExpandEnvironmentStringsW(f"{machine};{user}", buffer, len(buffer))
    env = {k: v for k, v in os.environ.items() if k.upper() != "PATH"}
    env["PATH"] = buffer.value      # Windows doesn't mind the case; pywinpty looks for this one
    return env


class Console:
    """One program in a pseudo-console, its screen read as plain text."""

    def __init__(self, argv: list[str], cwd: str, env: dict):
        self.process = PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(50, 220))
        self.screen = ""
        self._lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        while True:
            try:
                data = self.process.read(4096)
            except (EOFError, OSError):
                return
            if data:
                with self._lock:
                    self.screen += data

    def text(self) -> str:
        with self._lock:
            return ESCAPES.sub("", self.screen).replace("\r", "")

    def wait_for(self, what: str, seconds: float = 60) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if what in self.text():
                return
            time.sleep(0.2)
        raise AssertionError(f"waited {seconds}s for {what!r}; the console shows:\n{self.text()[-3000:]}")

    def type(self, line: str) -> None:
        self.process.write(line + "\r")

    def close(self) -> None:
        if self.process.isalive():
            self.process.terminate(force=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok' if ok else 'FAILED'}: {name}", flush=True)
    if not ok:
        if detail:
            print(detail, flush=True)
        sys.exit(1)


def main(butler: Path) -> None:
    env = new_window_environment()
    env.pop("BUTLER_PYTHON", None)
    check("Butler's bin is on the PATH a new window gets",
          any(os.path.normcase(p.rstrip("\\")) == os.path.normcase(str(butler / "bin")) for p in env["PATH"].split(";")),
          env["PATH"])

    home = str(Path.home())
    windows = Path(os.environ["SystemRoot"]) / "System32"
    cmd = [str(windows / "cmd.exe"), "/d", "/k"]
    powershell = [str(windows / "WindowsPowerShell" / "v1.0" / "powershell.exe"), "-NoLogo", "-NoProfile", "-NoExit"]
    for shell in (cmd, powershell):
        console = Console(shell, home, env)
        try:
            console.type("butler help")
            console.wait_for("butler doctor")
        finally:
            console.close()
        check(f"plain `butler` works in a new {'cmd' if shell == cmd else 'PowerShell'} window", True)

    # A key typed hidden. Typed one character at a time, as fingers (or a paste) deliver it.
    console = Console(cmd, home, env)
    try:
        console.type("butler secret butler-anthropic")
        console.wait_for("Your Claude API key › ")
        shown = len(console.text())
        for ch in TEST_KEY:
            console.process.write(ch)
            time.sleep(0.01)
        console.process.write("\r")
        console.wait_for("Stored butler-anthropic in Credential Manager.")
        check("the prompt's › shows in the console", "›" in console.text())
        check("the key never shows on screen", TEST_KEY not in console.text()[shown:], console.text()[shown:])
    finally:
        console.close()
    python = butler / ".venv" / "Scripts" / "python.exe"
    stored = subprocess.run([str(python), "-X", "utf8", "-c",
                             "from butler import system; import sys; "
                             f"sys.exit(0 if system.get_secret('butler-anthropic') == {TEST_KEY!r} else 1)"],
                            cwd=butler, env={**os.environ, "PYTHONPATH": str(butler / "src")})
    check("the key is in Credential Manager, as typed", stored.returncode == 0)
    # Back as a first day has it: with this stand-in key, the scheduled tick's morning brief (due
    # when CI runs after the brief's time) called Claude and logged a 401 (29 Oct 2026).
    check("the stand-in key comes out again", bool(ctypes.WinDLL("advapi32").CredDeleteW("butler-anthropic", 1, 0)))

    # The first run's questions, answered at the keyboard: no to the Terms stops it.
    console = Console(cmd, home, env)
    try:
        console.type("butler setup")
        # The name the installer took from the download's filename.
        name = (butler / ".butler-name").read_text(encoding="utf-8").strip()
        console.wait_for(f"Hello! I'm {name}, your new butler.")
        console.wait_for("Name › ")
        console.type("")
        console.wait_for("Press Enter to agree, or type no to stop › ", seconds=180)
        console.type("no")
        console.wait_for("Nothing has been sent anywhere.")
    finally:
        console.close()
    check("butler setup asks in the console and takes the answer", True)
    check("saying no records no agreement", not (butler / "data" / "terms.json").exists())

    # The persona tests read this console through their screen model (tests/personas/terminal.py):
    # a command typed a key at a time into PowerShell with PSReadLine (its profile loaded, as a
    # student's is) reads back once, not as every redraw ("bbutbutl...", round 3).
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "personas"))
    from terminal import Terminal
    window = Terminal([str(windows / "WindowsPowerShell" / "v1.0" / "powershell.exe"), "-NoLogo"], home, env)
    try:
        window.read(quiet=3.0, limit=60.0)
        for ch in "Write-Output persona-screen-check":
            window.keys(ch)
            time.sleep(0.03)
        window.keys("\r")
        screen = window.read(quiet=4.0, limit=120.0)
    finally:
        window.close()
    check("a typed command reads back once in the persona tests' screen",
          screen.count("Write-Output persona-screen-check") == 1 and "WWr" not in screen
          and "\npersona-screen-check" in screen, screen)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
