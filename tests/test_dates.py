"""Schedules across awkward nights: every five-minute tick through a DST change (clocks back, so
an hour repeats; clocks forward, so an hour never happens), year ends where ISO weeks wrap, and
leap years. Each email goes out exactly once, and the update check still happens. The clock is
simulated; nothing is sent. (The dates job in tests.yml reruns the whole suite at such moments.)
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from hypothesis import given
from hypothesis import strategies as st
from props import PROFILE

from butler import daily, tick, updates, weekly

assert PROFILE
UTC = timezone.utc


def clock(holder: list):
    """A datetime whose now() is the simulated moment in holder[0]."""
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return holder[0].astimezone(tz) if tz else holder[0].replace(tzinfo=None)
    return Clock


def ticks(start: datetime, end: datetime):
    t = start
    while t <= end:
        yield t
        t += timedelta(minutes=5)


def settings(zone: str, **email) -> dict:
    return {"timezone": zone, "name": "Bento", "email": {"send_hour": 7, "weekly_day": 6, "weekly_hour": 17,
                                                         "monthly_hour": 8, "recipients": ["sam@example.com"],
                                                         **email}}


class Tick(unittest.TestCase):
    """tick._weekly_if_due and _monthly_if_due, run on every simulated tick."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "out").mkdir()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.now = [datetime(2026, 1, 1, tzinfo=UTC)]
        self.weekly, self.monthly = [], []
        for p in (mock.patch.object(tick, "ROOT", self.root), mock.patch.object(tick, "datetime", clock(self.now)),
                  mock.patch.object(tick.daily, "_online", return_value=True),
                  mock.patch.object(tick.weekly, "run", side_effect=lambda s, d, dry_run: self.weekly.append(self.local(s))),
                  mock.patch.object(tick.monthly, "run", side_effect=lambda s, m, preview: self.monthly.append(m)),
                  mock.patch.object(tick.mailer, "send_or_queue", side_effect=AssertionError("nothing failed, so no note"))):
            p.start()
            self.addCleanup(p.stop)

    def local(self, s) -> datetime:
        return self.now[0].astimezone(ZoneInfo(s["timezone"]))

    def run_ticks(self, s: dict, start: datetime, end: datetime) -> None:
        with mock.patch.object(tick, "load_settings", return_value=s):
            for t in ticks(start, end):
                self.now[0] = t
                tick._weekly_if_due()
                tick._monthly_if_due()


class WeeklyAcrossDstTest(Tick):
    def test_lisbon_clocks_back_on_a_sunday_the_repeated_hour_sends_once(self):
        s = settings("Europe/Lisbon", weekly_hour=1)
        self.run_ticks(s, datetime(2026, 10, 24, 22, 0, tzinfo=UTC), datetime(2026, 10, 25, 5, 0, tzinfo=UTC))
        self.assertEqual(len(self.weekly), 1)
        self.assertEqual((self.weekly[0].hour, self.weekly[0].utcoffset()), (1, timedelta(hours=1)), "the first 01:00")

    def test_lisbon_clocks_forward_the_missing_hour_still_sends_once(self):
        s = settings("Europe/Lisbon", weekly_hour=1)
        self.run_ticks(s, datetime(2027, 3, 27, 22, 0, tzinfo=UTC), datetime(2027, 3, 28, 5, 0, tzinfo=UTC))
        self.assertEqual(len(self.weekly), 1)
        self.assertEqual(self.weekly[0].strftime("%H:%M"), "02:00", "01:00 never happens; 02:00 is the first tick after")

    def test_new_york_both_changes(self):
        for start in (datetime(2026, 11, 1, 4, 0, tzinfo=UTC), datetime(2027, 3, 14, 4, 0, tzinfo=UTC)):
            self.weekly.clear()
            s = settings("America/New_York", weekly_hour=2)
            self.run_ticks(s, start, start + timedelta(hours=6))
            self.assertEqual(len(self.weekly), 1, start)
            self.assertIn(self.weekly[0].hour, (2, 3), start)

    def test_a_friday_letter_on_the_weekend_the_clocks_change(self):
        s = settings("Europe/Lisbon", weekly_day=4, weekly_hour=9)
        self.run_ticks(s, datetime(2026, 10, 22, 0, 0, tzinfo=UTC), datetime(2026, 10, 26, 0, 0, tzinfo=UTC))
        self.assertEqual([w.date() for w in self.weekly], [date(2026, 10, 23)])

    def test_unset_day_and_hour_mean_sunday_at_17(self):
        s = settings("Europe/Lisbon")
        del s["email"]["weekly_day"], s["email"]["weekly_hour"]
        self.run_ticks(s, datetime(2026, 10, 24, 0, 0, tzinfo=UTC), datetime(2026, 10, 26, 0, 0, tzinfo=UTC))
        self.assertEqual([w.strftime("%a %H:%M") for w in self.weekly], ["Sun 17:00"])

    def test_iso_weeks_wrapping_at_year_end(self):
        """2026 has 53 ISO weeks: Sunday 3 January 2027 is in week 53 of 2026. Each Sunday sends once."""
        s = settings("Europe/Lisbon")
        self.run_ticks(s, datetime(2026, 12, 26, 0, 0, tzinfo=UTC), datetime(2027, 1, 11, 0, 0, tzinfo=UTC))
        self.assertEqual([w.date() for w in self.weekly], [date(2026, 12, 27), date(2027, 1, 3), date(2027, 1, 10)])
        markers = sorted(p.name for p in (self.root / "out").glob("weekly-sent-*"))
        self.assertEqual(markers, ["weekly-sent-2026-52", "weekly-sent-2026-53", "weekly-sent-2027-01"])


class MonthlyTest(Tick):
    def test_new_year_looks_back_at_december(self):
        self.run_ticks(settings("Europe/Lisbon"), datetime(2026, 12, 31, 20, 0, tzinfo=UTC),
                       datetime(2027, 1, 2, 0, 0, tzinfo=UTC))
        self.assertEqual(self.monthly, [date(2026, 12, 1)])

    def test_after_a_leap_february(self):
        self.run_ticks(settings("Europe/Lisbon"), datetime(2028, 2, 29, 0, 0, tzinfo=UTC),
                       datetime(2028, 3, 2, 0, 0, tzinfo=UTC))
        self.assertEqual(self.monthly, [date(2028, 2, 1)])

    def test_the_first_falls_on_a_dst_sunday(self):
        """1 November 2026 in New York: the clocks go back that morning, and it's a Sunday."""
        self.run_ticks(settings("America/New_York", monthly_hour=1), datetime(2026, 11, 1, 3, 0, tzinfo=UTC),
                       datetime(2026, 11, 2, 3, 0, tzinfo=UTC))
        self.assertEqual(self.monthly, [date(2026, 10, 1)])
        self.assertEqual(len(self.weekly), 1)


class BriefAcrossDstTest(unittest.TestCase):
    """daily.main --scheduled on every tick: one brief per local day, whatever the clocks do."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.now = [datetime(2026, 1, 1, tzinfo=UTC)]
        self.sent = []
        brief = SimpleNamespace(charts={}, subject="Bento", updates=[], news=[])
        quiet = SimpleNamespace(basicConfig=lambda **k: None, INFO=20, StreamHandler=lambda: None,
                                getLogger=lambda *a: SimpleNamespace(addHandler=lambda h: None))
        for p in (mock.patch.object(daily, "ROOT", self.root), mock.patch.object(daily, "datetime", clock(self.now)),
                  mock.patch.object(daily, "logging", quiet), mock.patch.object(daily, "_online", return_value=True),
                  mock.patch.object(daily, "build", return_value=brief),
                  mock.patch.object(daily, "to_html", return_value="<p>brief</p>"),
                  mock.patch.object(daily, "to_text", return_value="brief"),
                  mock.patch.object(daily.llm, "record_news"),
                  mock.patch.object(daily.mailer, "send_or_queue", side_effect=lambda *a, **k: self.sent.append(self.now[0]) or True),
                  mock.patch.object(sys, "argv", ["daily", "--scheduled"])):
            p.start()
            self.addCleanup(p.stop)

    def run_ticks(self, s, start, end):
        """The day before is already sent: only the night itself is under test."""
        (self.root / "out").mkdir(exist_ok=True)
        (self.root / "out" / f"sent-{start.astimezone(ZoneInfo(s['timezone'])).date()}").touch()
        with mock.patch.object(daily, "load_settings", return_value=s):
            for t in ticks(start, end):
                self.now[0] = t
                daily.main()

    def test_send_hour_in_the_missing_hour(self):
        self.run_ticks(settings("Europe/Lisbon", send_hour=1), datetime(2027, 3, 27, 22, 0, tzinfo=UTC),
                       datetime(2027, 3, 28, 6, 0, tzinfo=UTC))
        local = [t.astimezone(ZoneInfo("Europe/Lisbon")).strftime("%d %H:%M") for t in self.sent]
        self.assertEqual(local, ["28 02:00"])

    def test_send_hour_in_the_repeated_hour(self):
        self.run_ticks(settings("Europe/Lisbon", send_hour=1), datetime(2026, 10, 24, 22, 0, tzinfo=UTC),
                       datetime(2026, 10, 25, 6, 0, tzinfo=UTC))
        self.assertEqual(len(self.sent), 1)

    def test_a_brief_a_day_through_a_week_with_a_dst_change(self):
        self.run_ticks(settings("Europe/Lisbon"), datetime(2026, 10, 21, 23, 30, tzinfo=UTC),
                       datetime(2026, 10, 29, 0, 0, tzinfo=UTC))
        days = [t.astimezone(ZoneInfo("Europe/Lisbon")) for t in self.sent]
        self.assertEqual([d.day for d in days], [23, 24, 25, 26, 27, 28])
        self.assertTrue(all(d.hour == 7 and d.minute == 0 for d in days), days)


class UpdateCheckAcrossDstTest(unittest.TestCase):
    """The once-a-day update check at update_hour, on the nights that hour repeats or never comes."""

    def setUp(self):
        self.state = {}
        for p in (mock.patch.object(updates, "can_update", return_value=None),
                  mock.patch.object(updates, "load_state", side_effect=lambda: dict(self.state)),
                  mock.patch.object(updates, "save_state", side_effect=lambda s: self.state.update(s)),
                  mock.patch.object(updates, "install_id", return_value="x"),
                  mock.patch.object(updates, "installed_version", return_value="v0.1.0")):
            p.start()
            self.addCleanup(p.stop)

    def checks(self, zone, hour, start, end, last_checked=None) -> list[datetime]:
        """The ticks that checked, from start to end. By default every check due before start was
        done, as on a Mac that has been running."""
        self.state["checked"] = last_checked or updates._check_day(start.astimezone(ZoneInfo(zone)), hour)
        seen = []
        with mock.patch.object(updates, "fetch_manifest", side_effect=lambda: seen.append(now) or {"available": False}):
            for t in ticks(start, end):
                now = t.astimezone(ZoneInfo(zone))
                updates.tick({"updates": "auto", "update_hour": hour}, now)
        return seen

    def test_the_skipped_hour_still_checks_once(self):
        seen = self.checks("America/New_York", 2, datetime(2027, 3, 14, 4, 0, tzinfo=UTC),
                           datetime(2027, 3, 14, 12, 0, tzinfo=UTC))
        self.assertEqual([t.strftime("%H:%M") for t in seen], ["03:00"])

    def test_the_repeated_hour_checks_once(self):
        seen = self.checks("America/New_York", 1, datetime(2026, 11, 1, 3, 0, tzinfo=UTC),
                           datetime(2026, 11, 1, 12, 0, tzinfo=UTC))
        self.assertEqual(len(seen), 1)

    def test_an_ordinary_night_checks_at_the_hour_and_not_the_hour_after(self):
        seen = self.checks("Europe/Lisbon", 3, datetime(2026, 10, 12, 0, 0, tzinfo=UTC),
                           datetime(2026, 10, 13, 0, 0, tzinfo=UTC))
        self.assertEqual([t.strftime("%H:%M") for t in seen], ["03:00"])

    def test_a_mac_asleep_at_the_hour_checks_when_it_wakes(self):
        # Setup wakes the Mac at 06:59 for the brief; it slept through 3 a.m.
        seen = self.checks("Europe/Lisbon", 3, datetime(2026, 10, 12, 5, 59, tzinfo=UTC),
                           datetime(2026, 10, 13, 1, 0, tzinfo=UTC), last_checked="2026-10-11")
        self.assertEqual([t.strftime("%d %H:%M") for t in seen], ["12 06:59"])

    def test_a_late_hour_slept_through_is_checked_the_next_morning(self):
        # update_hour 23, and the Mac slept through it: the missed check comes at 06:59, and that
        # evening's on the first tick after 23:00.
        seen = self.checks("Europe/Lisbon", 23, datetime(2026, 10, 13, 5, 59, tzinfo=UTC),
                           datetime(2026, 10, 13, 23, 0, tzinfo=UTC), last_checked="2026-10-11")
        self.assertEqual([t.strftime("%d %H:%M") for t in seen], ["13 06:59", "13 23:04"])


class LetterDayProperties(unittest.TestCase):
    @given(st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 12, 31)), st.integers(0, 6))
    def test_next_letter_day_is_this_week_on_the_right_day(self, today, weekday):
        day = weekly.next_letter_day({"email": {"weekly_day": weekday}}, today)
        self.assertEqual(day.weekday(), weekday)
        self.assertTrue(0 <= (day - today).days <= 6)


if __name__ == "__main__":
    unittest.main()
