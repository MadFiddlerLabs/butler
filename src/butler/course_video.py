"""Each module's video, opened in the student's browser as the module starts: the course is watched
first, then done (the owner's call, 29 Oct 2026). Module 0 opens in the first run, Module 1 the
first time `butler claude` runs, and each `/butler-setup` step's module through `butler watch`.

Once per module: data/course-videos-opened lists the modules opened, one number a line, so a
resumed step doesn't open its video again. scripts/first-run.sh does the same for Module 0 in bash,
before Butler's Python exists, in the same words (tests/test_first_run_python.py compares them).
"""
import os
from pathlib import Path

from . import progress, system
from .config import ROOT

READY = "Press Enter when you're ready › "


DATA = ROOT / "data"


def opened_file(data: Path | None = None) -> Path:
    return (data or DATA) / "course-videos-opened"


def url(module: int) -> str:
    """The module's page on the course website, at its video (the page's #video)."""
    return progress.module_url(module) + "#video"


def opened(data: Path | None = None) -> set[int]:
    try:
        return {int(n) for n in opened_file(data).read_text().split() if n.isdigit()}
    except OSError:
        return set()


def open_page(module: int, data: Path | None = None) -> list[str]:
    """Opens the module's video in the browser (only says where it is with BUTLER_NO_OPEN, as in
    tests), records it, and returns the lines to tell the student."""
    link, title = url(module), progress.course_modules()[module]["title"]
    if os.environ.get("BUTLER_NO_OPEN"):
        where = f"Module {module}'s video, {title}, is here:\n{link}"
    else:
        system.open_file(link)
        where = f"I've opened Module {module}'s video in your browser: {title}."
    opened_file(data).parent.mkdir(exist_ok=True)
    with opened_file(data).open("a", encoding="utf-8") as f:
        f.write(f"{module}\n")
    return where.split("\n") + ["Please watch it first: it shows what we're about to do. "
                                "If you'd like to read along, the lesson is on the same page, after the video."]
