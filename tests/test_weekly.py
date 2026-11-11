"""Unit: the weekly letter goes out on email.weekly_day, Sunday unless set. A late letter or
look-back (tick.py's catch-up) says so at the top.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from butler import monthly, weekly
from butler.finance import SPEND, Tx

LATE = "This letter is late. It was due yesterday, Sunday at 17:00, but the Mac was asleep or offline then."


class LetterDayTest(unittest.TestCase):
    def test_sunday_unless_set(self):
        self.assertEqual(weekly.letter_day_name({}), "Sunday")
        self.assertEqual(weekly.letter_day_name({"email": {"weekly_day": 4}}), "Friday")
        self.assertEqual(weekly.letter_day_name({"email": {"weekly_day": 0}}), "Monday")

    def test_next_letter_day_counts_today(self):
        wednesday = date(2026, 10, 14)
        self.assertEqual(weekly.next_letter_day({}, wednesday), date(2026, 10, 18))
        self.assertEqual(weekly.next_letter_day({"email": {"weekly_day": 4}}, wednesday), date(2026, 10, 16))
        self.assertEqual(weekly.next_letter_day({"email": {"weekly_day": 2}}, wednesday), wednesday)
        self.assertEqual(weekly.next_letter_day({"email": {"weekly_day": 1}}, wednesday), date(2026, 10, 20))

    def test_part_of_day(self):
        self.assertEqual([weekly._part_of_day(h) for h in (7, 12, 17, 18)],
                         ["morning", "afternoon", "afternoon", "evening"])


class LateLetterTest(unittest.TestCase):
    """Invented: the Johnsons' letter for Sunday 18 October 2026, sent a day late."""

    def test_the_late_line_tops_both_parts_of_the_letter(self):
        w = SimpleNamespace(last_day=date(2026, 10, 18))
        secs = [("Projects", ["Home: on track."])]
        self.assertIn(LATE, weekly.to_text(w, secs, LATE).split("PROJECTS")[0])
        self.assertIn(LATE, weekly.to_html(w, secs, banner=LATE).split("Projects")[0])
        self.assertNotIn("late", weekly.to_text(w, secs) + weekly.to_html(w, secs))

    def test_the_late_line_tops_the_look_back(self):
        mo = SimpleNamespace(start=date(2026, 10, 1))
        late = LATE.replace("letter", "look-back")
        self.assertIn(late, monthly.to_text(mo, [("For November", ["Sam: renew the permit"])], late))
        self.assertIn(late, monthly.to_html(mo, [], banner=late))

    def test_run_puts_the_late_line_in_the_letter(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(weekly, "ROOT", Path(tmp)), \
             mock.patch.object(weekly, "build_week", return_value=SimpleNamespace(last_day=date(2026, 10, 18), money=None)), \
             mock.patch.object(weekly, "write", return_value={}), mock.patch.object(weekly.projects, "load", return_value=[]), \
             mock.patch.object(weekly, "sections", return_value=[("Projects", ["Home: on track."])]), \
             mock.patch.object(weekly.charts, "month_to_date", return_value=b"png"), \
             mock.patch.object(weekly.charts, "categories", return_value=None), \
             mock.patch.object(weekly.finance, "load_rules", return_value={"rules": {"day_to_day": []}}):
            weekly.run({}, date(2026, 10, 18), dry_run=True, late=LATE)
            preview = Path(tmp) / "out" / "preview"
            self.assertIn(LATE, (preview / "weekly-2026-42.txt").read_text())
            self.assertIn(LATE, (preview / "weekly-2026-42.html").read_text())


class SignatureTest(unittest.TestCase):
    """Invented: the Johnsons' letter for Sunday 18 October 2026."""
    W = SimpleNamespace(last_day=date(2026, 10, 18))
    SECS = [("Projects", ["Home: on track."])]

    def test_off_unless_the_household_says_true(self):
        for settings in ({}, {"email": {}}, {"email": {"sign_letter": False}}, {"email": {"sign_letter": "yes"}}, {"email": None}):
            self.assertFalse(weekly.signs_letter(settings), settings)
        self.assertTrue(weekly.signs_letter({"email": {"sign_letter": True}}))

    def test_an_unsigned_letter_has_no_line_and_no_link(self):
        both = weekly.to_text(self.W, self.SECS) + weekly.to_html(self.W, self.SECS)
        self.assertNotIn("buildyourbutler.com", both)
        self.assertNotIn(weekly.SIGNATURE, both)

    def test_a_signed_letter_ends_with_the_line_and_a_tagged_link(self):
        text = weekly.to_text(self.W, self.SECS, signed=True)
        self.assertTrue(text.endswith(f"{weekly.SIGNATURE} {weekly.SIGNATURE_URL}"))
        html = weekly.to_html(self.W, self.SECS, signed=True)
        self.assertIn(f'href="{weekly.SIGNATURE_URL.replace("&", "&amp;")}"', html)
        self.assertGreater(html.index(weekly.SIGNATURE), html.index("Reply to answer a question"))
        self.assertEqual(weekly.SIGNATURE_URL, "https://buildyourbutler.com/?utm_source=butler_letter&utm_medium=email",
                         "the link carries nothing about the household")

    def test_run_signs_the_letter_only_when_set(self):
        for sign, expected in ((True, True), (False, False)):
            with tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(weekly, "ROOT", Path(tmp)), \
                 mock.patch.object(weekly, "build_week", return_value=SimpleNamespace(last_day=date(2026, 10, 18), money=None)), \
                 mock.patch.object(weekly, "write", return_value={}), mock.patch.object(weekly.projects, "load", return_value=[]), \
                 mock.patch.object(weekly, "sections", return_value=self.SECS), \
                 mock.patch.object(weekly.charts, "month_to_date", return_value=b"png"), \
                 mock.patch.object(weekly.charts, "categories", return_value=None), \
                 mock.patch.object(weekly.finance, "load_rules", return_value={"rules": {"day_to_day": []}}):
                weekly.run({"email": {"sign_letter": sign}}, date(2026, 10, 18), dry_run=True)
                preview = Path(tmp) / "out" / "preview"
                for name in ("weekly-2026-42.txt", "weekly-2026-42.html"):
                    self.assertEqual(weekly.SIGNATURE in (preview / name).read_text(), expected, (sign, name))


def grocer(day: date, amount: float, **kw) -> Tx:
    return Tx(id=1, day=day, created=datetime(2026, 10, 1, tzinfo=timezone.utc), payee="Grocer",
              account="Everyday", amount=amount, kind=SPEND, category="Food", **kw)


class TypicalWeekTest(unittest.TestCase):
    """The typical week divides by the history there is, not by 8 weeks of it. Invented amounts."""
    RULES = {"rules": {"day_to_day": ["Food"]}}
    SUNDAY = date(2026, 10, 18)

    def numbers(self, txs):
        return weekly._week_numbers(SimpleNamespace(txs=txs), self.RULES, self.SUNDAY)

    def test_two_weeks_of_history_is_two_weeks(self):
        first = self.SUNDAY - timedelta(days=20)          # 14 days before the week
        txs = [grocer(first, 100), grocer(first + timedelta(days=7), 100), grocer(self.SUNDAY, 30),
               grocer(first - timedelta(days=30), 500, one_off=True),
               grocer(first - timedelta(days=30), 90, fixed=True)]
        self.assertEqual(self.numbers(txs), (30, 100))

    def test_a_full_eight_weeks(self):
        txs = [grocer(self.SUNDAY - timedelta(days=62), 400), grocer(self.SUNDAY - timedelta(days=63), 1000)]
        self.assertEqual(self.numbers(txs), (0, 50))

    def test_under_a_week_of_history_has_no_typical_week(self):
        txs = [grocer(self.SUNDAY - timedelta(days=10), 100), grocer(self.SUNDAY, 30)]
        self.assertEqual(self.numbers(txs), (30, None))



class YenThresholdTest(unittest.TestCase):
    """A category is ahead of plan by 100 euro-sized units: ¥15,000 for a yen household."""

    def rows(self, spent: float, scale: float):
        m = SimpleNamespace(budget_by_category={"Rent": 15_000}, against_budget_by_category={"Rent": spent},
                            daily_budget_by_category={}, daily_by_category={}, pace_fraction=0.5)
        with mock.patch("butler.weekly.scale", return_value=scale):
            return weekly._category_rows(m, {"rules": {"day_to_day": []}})

    def test_a_few_thousand_yen_over_is_on_track(self):
        self.assertEqual(self.rows(25_000, 150), ([], ["Rent"]))
        self.assertEqual([r.category for r in self.rows(30_001, 150)[0]], ["Rent"])
        self.assertEqual([r.category for r in self.rows(25_000, 1)[0]], ["Rent"])


if __name__ == "__main__":
    unittest.main()
