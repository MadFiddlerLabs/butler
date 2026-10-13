"""What launchd runs every 5 minutes: re-send anything Gmail refused earlier (mailer.py), handle
replies, check for updates once a day (see updates.py), report finished setup steps to the course
(progress.py), send the weekly letter and the monthly look-back if due, then the daily brief if due.
Calendar copying and colors live in Apps Script (apps-script/butler-calendar.gs)."""

import logging
import sys

from datetime import datetime

from . import daily, mailer, monthly, progress, replies, updates, weekly
from .config import ROOT, brand, load_settings, tz

log = logging.getLogger("butler")


def main() -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(filename=ROOT / "logs" / "butler.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
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


def _weekly_if_due() -> None:
    settings = load_settings()
    now = datetime.now(tz(settings))
    # Sunday at 17:00 unless set, as docs/CONFIG.md says. The hour is "at or after", so a night the
    # clocks change can't skip it.
    if now.weekday() != weekly.letter_weekday(settings) or now.hour < settings["email"].get("weekly_hour", 17):
        return
    year, week, _ = now.isocalendar()
    marker = ROOT / "out" / f"weekly-sent-{year}-{week:02d}"
    if marker.exists() or not daily._online():
        return
    try:
        weekly.run(settings, now.date(), dry_run=False)
    except Exception as e:
        log.exception("weekly failed")
        mailer.send_or_queue(settings, f"weekly-failed-{year}-{week:02d}",
                             f"{brand(settings)} · {now:%A} letter failed {now:%-d %B}",
                             f"This week's letter failed to build ({e}). Details are in logs/butler.log.")
    marker.touch()


def _monthly_if_due() -> None:
    from datetime import timedelta
    settings = load_settings()
    now = datetime.now(tz(settings))
    if now.day != 1 or now.hour < settings["email"].get("monthly_hour", 8):
        return
    last = (now.date() - timedelta(days=1)).replace(day=1)
    marker = ROOT / "out" / f"monthly-sent-{last:%Y-%m}"
    if marker.exists() or not daily._online():
        return
    try:
        monthly.run(settings, last, preview=False)
    except Exception as e:
        log.exception("monthly failed")
        mailer.send_or_queue(settings, f"monthly-failed-{last:%Y-%m}", f"{brand(settings)} · monthly look-back failed",
                             f"The {last:%B} look-back failed ({e}). Details are in logs/butler.log.")
    marker.touch()


if __name__ == "__main__":
    main()
