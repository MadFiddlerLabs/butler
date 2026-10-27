"""Entry point for people and the scheduler on Windows: run.sh's twin, in Python, because
Windows has no bash. On a Mac, run.sh stays the entry point.
  .venv\\Scripts\\python.exe run.py preview      build this morning's brief and open it; sends nothing
  .venv\\Scripts\\python.exe run.py daily [--dry-run] [--no-llm] [--date YYYY-MM-DD]
  .venv\\Scripts\\python.exe run.py tick | portal | weekly | monthly | replies
  .venv\\Scripts\\python.exe run.py setup [--again]  the guided first run (butler.first_run)
  .venv\\Scripts\\python.exe run.py cli <args>       the `butler` command: bin\\butler.cmd calls this

Butler reads and writes UTF-8 everywhere, and Windows' default is its ANSI code page, so this
runs Butler in Python's UTF-8 mode (-X utf8), starting itself again in it if it has to."""
import os
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main(argv: list[str]) -> int:
    if not sys.flags.utf8_mode:
        return subprocess.run([sys.executable, "-X", "utf8", __file__, *argv]).returncode
    os.chdir(ROOT)
    if sys.stdout is None:
        # pythonw, as Task Scheduler runs it, has nowhere to print. launchd's jobs write here too.
        (ROOT / "logs").mkdir(exist_ok=True)
        sys.stdout = sys.stderr = open(ROOT / "logs" / "scheduled.log", "a", encoding="utf-8", buffering=1)
    sys.path.insert(0, str(ROOT / "src"))
    cmd, args = (argv[0], argv[1:]) if argv else ("daily", [])
    from butler import system
    if cmd == "preview":
        # Until there's a Claude key, it leaves out the parts Claude writes.
        nollm = [] if system.has_secret("butler-anthropic") else ["--no-llm"]
        code = _module("butler.daily", ["--dry-run", *nollm, *args])
        briefs = sorted((ROOT / "out" / "preview").glob("brief-*.html"), key=lambda p: p.stat().st_mtime)
        if code == 0 and briefs and not os.environ.get("BUTLER_NO_OPEN"):
            system.open_file(str(briefs[-1]))
        return code
    if cmd == "setup":
        return _module("butler.first_run", args)
    if cmd == "tick":
        # The scheduled run keeps the computer from sleeping until it's done, as caffeinate does
        # in run.sh: a wake for the brief can otherwise drop back to sleep mid-send.
        with system.keep_awake():
            return _module("butler.tick", args)
    return _module(f"butler.{cmd}", args)


def _module(name: str, args: list[str]) -> int:
    sys.argv = [name, *args]
    try:
        runpy.run_module(name, run_name="__main__", alter_sys=True)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
