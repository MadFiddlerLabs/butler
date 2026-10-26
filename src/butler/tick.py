"""What launchd runs every 5 minutes: re-send anything Gmail refused earlier (mailer.py), handle
replies, check for updates once a day (see updates.py), report finished setup steps to the course
(progress.py), send the weekly letter and the monthly look-back if due, then the daily brief if due.
Calendar copying and colors live in Apps Script (apps-script/butler-calendar.gs)."""

import logging
import sys

from datetime import date, datetime, timedelta

from . import daily, mailer, monthly, progress, replies, updates, weekly
from .config import ROOT, brand, load_settings, tz
from .fmt import strftime

log = logging.getLogger("butler")


def main() -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(filename=ROOT / "logs" / "butler.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not (ROOT / "config" / "settings.yaml").exists():
        # Without the household's own settings, everything would run on the invented example
        # household. That happens after "Start fresh" moves the settings to a backup while the
        # schedule still runs: wait quietly for the new ones, and send nothing.
        log.info("tick: no config/settings.yaml yet, so nothing to do")
        return
    try:
        mailer.flush(load_settings())   # anything Gmail refused earlier; hourly while it keeps refusing
    except Exception:
        log.exception("outbox failed")
    try:
        replies.process(load_settings())
    except Exception:
        log.exception("replies failed")  # never let replies block the brief
    try:
        settings = load_settings()
        updates.tick(settings, datetime.now(tz(settings)))
    except Exception:
        log.exception("update check failed")  # never let updates block the brief
    progress.sync()  # never raises; queues whatever can't be sent yet
    for name, letter in (("weekly letter", _weekly_if_due), ("monthly look-back", _monthly_if_due)):
        try:
            letter()
        except Exception:
            log.exception("%s failed", name)  # never let a letter block the brief
    sys.argv = [sys.argv[0], "--scheduled"]
    daily.main()


CATCH_UP_DAYS = 2   # a letter the Mac slept through still goes out up to this many days late


def _weekly_if_due() -> None:
    settings = load_settings()
    now = datetime.now(tz(settings))
    # Sunday at 17:00 unless set, as docs/CONFIG.md says. The hour is "at or after", so a night the
    # clocks change can't skip it.
    hour = settings["email"].get("weekly_hour", 17)
    day = now.date() - timedelta(days=(now.weekday() - weekly.letter_weekday(settings)) % 7)
    if day == now.date() and now.hour < hour:
        return
    late = (now.date() - day).days
    year, week, _ = day.isocalendar()
    marker = ROOT / "out" / f"weekly-sent-{year}-{week:02d}"
    if late > CATCH_UP_DAYS or marker.exists() or (late and not _running_before(day)) or not daily._online():
        return
    try:
        weekly.run(settings, day, dry_run=False, late=_late_note("letter", late, day, hour))
    except Exception as e:
        log.exception("weekly failed")
        mailer.send_or_queue(settings, f"weekly-failed-{year}-{week:02d}",
                             f"{brand(settings)} · {day:%A} letter failed {strftime(now, '%-d %B')}",
                             f"This week's letter failed to build ({e}). Details are in logs/butler.log.")
    marker.touch()


def _monthly_if_due() -> None:
    settings = load_settings()
    now = datetime.now(tz(settings))
    hour = settings["email"].get("monthly_hour", 8)
    if now.day == 1 and now.hour < hour:
        return
    late = now.day - 1
    last = (now.date().replace(day=1) - timedelta(days=1)).replace(day=1)
    marker = ROOT / "out" / f"monthly-sent-{last:%Y-%m}"
    due = now.date().replace(day=1)
    if late > CATCH_UP_DAYS or marker.exists() or (late and not _running_before(due)) or not daily._online():
        return
    try:
        monthly.run(settings, last, preview=False, late=_late_note("look-back", late, due, hour))
    except Exception as e:
        log.exception("monthly failed")
        mailer.send_or_queue(settings, f"monthly-failed-{last:%Y-%m}", f"{brand(settings)} · monthly look-back failed",
                             f"The {last:%B} look-back failed ({e}). Details are in logs/butler.log.")
    marker.touch()


def _running_before(day: date) -> bool:
    """Butler was already sending briefs before `day`. A catch-up is for a Mac that slept through
    a letter, not for a household that set Butler up after it was due."""
    return any(p.name < f"sent-{day}" for p in (ROOT / "out").glob("sent-*"))


def _late_note(what: str, days: int, due: date, hour: int) -> str:
    """The line a late letter opens with, or nothing when it's on time."""
    if not days:
        return ""
    when = "yesterday" if days == 1 else f"{days} days ago"
    return (f"This {what} is late. It was due {when}, {due:%A} at {hour}:00, "
            "but the Mac was asleep or offline then.")


if __name__ == "__main__":
    main()
