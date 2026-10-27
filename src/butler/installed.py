"""Runs right after the Windows installer puts Butler in %USERPROFILE%\\Butler and bootstrap.ps1
has made its Python: the Mac's postinstall (packaging/macos/scripts/postinstall), for Windows.

  A fresh install: names the butler from the installer's filename (the website serves it as
  "<Name>.exe") and opens the guided first run in a console window.
  Over an existing Butler (its first run started or finished): changes nothing of the
  household's and opens the choice to resume where you left off, or start fresh.
  A silent install (the overnight updater, CI) opens nothing.

Every outcome goes to logs/install.log, so a window that didn't open can be explained. It never
fails the install.

  python run.py installed --installer "<the installer's path>" [--silent]"""
import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

from .config import ROOT
from .setup_settings import valid_name

# The first run's choice over an existing Butler, as the Mac's welcome-back.command offers it.
OPENS = {"fresh": ["setup"], "started": ["setup", "--again"], "finished": ["setup", "--again"]}


def install_state(root: Path) -> str:
    """scripts/lib/install.sh's install_state: fresh, started or finished."""
    if (root / "data" / "first-run-done").exists():
        return "finished"
    settings = (root / "config" / "settings.yaml").exists()
    if settings and any((root / "out" / "preview").glob("brief-*.html")):
        return "finished"
    if settings or (root / "data" / "first-run-started").exists():
        return "started"
    return "fresh"


def name_from_installer(path: str) -> str | None:
    """The butler's name from the downloaded installer's filename, or None. Browsers add " (1)"
    to a repeat download; "Butler.exe" and "Butler-<anything>.exe" are the generic download."""
    base = PureWindowsPath(path).name
    base = re.sub(r"\.exe$", "", base, flags=re.IGNORECASE)
    base = re.sub(r" \(\d+\)$|-\d+$", "", base).replace("%20", " ")
    if base == "Butler" or base.startswith("Butler-"):
        return None
    return valid_name(base)


def _note(root: Path, line: str) -> None:
    version = (root / ".butler-version").read_text().strip() if (root / ".butler-version").exists() else "dev"
    (root / "logs").mkdir(exist_ok=True)
    with open(root / "logs" / "install.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {version} {line}\n")


def open_console(root: Path, args: list[str]) -> None:
    """A new console window running `butler <args>`, as Terminal opens a .command on a Mac."""
    butler = root / "bin" / "butler.cmd"
    subprocess.Popen(["cmd.exe", "/c", "start", "Butler", "cmd.exe", "/k", str(butler), *args], cwd=root)


def run(root: Path, installer: str, silent: bool) -> str:
    state = install_state(root)
    name = name_from_installer(installer)
    if name and state == "fresh":
        (root / ".butler-name").write_text(name + "\n", encoding="utf-8")
    if silent:
        return f"installed ({state}), silently: no window opened"
    if not (root / "bin" / "butler.cmd").exists():
        return f"installed ({state}): bin\\butler.cmd is missing, so the first run didn't open"
    args = OPENS[state]
    open_console(root, args)
    return f"installed ({state}): opened a console with butler {' '.join(args)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="butler.installed")
    parser.add_argument("--installer", default="")
    parser.add_argument("--silent", action="store_true")
    a = parser.parse_args(argv)
    try:
        line = run(ROOT, a.installer, a.silent)
    except Exception as e:  # noqa: BLE001 - never fail the install over this
        line = f"installed, but the step after it failed ({type(e).__name__}: {e})"
    _note(ROOT, line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
