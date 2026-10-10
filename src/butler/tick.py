"""What launchd runs every 5 minutes: handle replies, check for updates once a day (see
updates.py), send the Sunday letter if it's due, then send the daily brief if it's due.
Calendar copying and colors live in Apps Script (apps-script/butler-calendar.gs)."""

import logging
import sys

from datetime import datetime

from . import daily, mailer, monthly, replies, updates, weekly
from .config import ROOT, brand, load_settings, tz

log = logging.getLogger("butler")


def main() -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(filename=ROOT / "logs" / "butler.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        replies.process(load_settings())
    except Exception:
        log.exception("replies failed")  # never let replies block the brief
    try:
        settings = load_settings()
        updates.tick(settings, datetime.now(tz(settings)))
    except Exception:
        log.exception("update check failed")  # never let updates block the brief
    _weekly_if_due()
    _monthly_if_due()
    sys.argv = [sys.argv[0], "--scheduled"]
    daily.main()


def _weekly_if_due() -> None:
    settings = load_settings()
    cfg = settings["email"]
    now = datetime.now(tz(settings))
    if now.weekday() != cfg["weekly_day"] or now.hour < cfg["weekly_hour"]:
        return
    year, week, _ = now.isocalendar()
    marker = ROOT / "out" / f"weekly-sent-{year}-{week:02d}"
    if marker.exists() or not daily._online():
        return
    try:
        weekly.run(settings, now.date(), dry_run=False)
    except Exception as e:
        log.exception("weekly failed")
        mailer.send(settings, f"{brand(settings)} · Sunday letter failed {now:%-d %B}",
                    f"This week's letter failed to build or send ({e}). Details are in logs/butler.log.")
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
        mailer.send(settings, f"{brand(settings)} · monthly look-back failed",
                    f"The {last:%B} look-back failed ({e}). Details are in logs/butler.log.")
    marker.touch()


if __name__ == "__main__":
    main()
