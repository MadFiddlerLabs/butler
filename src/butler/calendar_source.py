"""Where Calendar and Reminders come from: Apple's, through the Swift helper (bin/butler-ek, the
default), or Google Calendar and Google Tasks (gcal.py) with `calendar.source: google`. Both
answer the helper's nine commands with the same JSON, so callers never know which one ran.

    calendar_source.run(["events", "--start", ISO, "--end", ISO], timeout=60)   # JSON text
"""

import json
import subprocess

from . import config

SOURCES = ("apple", "google")


class Failed(RuntimeError):
    """The calendar answered with an error: the helper's message, or Google's."""


def source(settings: dict | None = None) -> str:
    """The household's calendar: apple unless settings say google. An absent setting is apple,
    so households set up before Google existed run exactly as they did."""
    settings = config.load_settings() if settings is None else settings
    return str((settings.get("calendar") or {}).get("source") or "apple").strip().lower()


def run(args: list[str], timeout: float = 60, settings: dict | None = None) -> str:
    """Runs one helper command and returns its JSON text. A part not connected yet raises
    config.NotSetUp; an error from the calendar raises Failed."""
    which = source(settings)
    if which == "google":
        from . import gcal
        return gcal.run(list(args), timeout)
    if which != "apple":
        raise Failed(f"calendar.source is '{which}'; it takes {' or '.join(SOURCES)}")
    out = subprocess.run([config.butler_ek(), *args], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise Failed(out.stderr.strip() or "butler-ek failed")
    return out.stdout


def ask(args: list[str], timeout: float = 60, settings: dict | None = None):
    """run(), parsed."""
    return json.loads(run(args, timeout, settings))
