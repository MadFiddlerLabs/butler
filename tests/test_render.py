"""Unit: what the Money section says about the cash drawer.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

from butler import fmt, render


def drawer(estimate: float, today: date) -> str:
    """The drawer line for the example household (Pat is paid €180 in cash on Fridays)."""
    m = SimpleNamespace(drawer=estimate, drawer_counted=date(2026, 10, 9), today=today)
    return " ".join(render.drawer_lines(m))


class DrawerLineTest(unittest.TestCase):
    WEDNESDAY, THURSDAY = date(2026, 10, 14), date(2026, 10, 15)

    def test_a_positive_estimate_is_an_upper_bound(self):
        line = drawer(260, self.WEDNESDAY)
        self.assertIn("Cash drawer: about €260 at most (counted 9 Oct, plus top-ups, less Pat (nanny)).", line)

    def test_a_negative_estimate_asks_for_a_count_instead(self):
        # A withdrawal Butler didn't recognise, or a stale count: never "about −€140 at most".
        line = drawer(-140, self.WEDNESDAY)
        self.assertNotIn("−", line)
        self.assertNotIn("at most", line)
        self.assertIn("Cash drawer: needs a count.", line)
        self.assertIn("More went to Pat (nanny) since 9 Oct", line)

    def test_the_payday_nudge_still_comes_when_the_count_is_off(self):
        self.assertIn("Pat (nanny) (€180) is due tomorrow: Alex, withdraw cash.", drawer(-140, self.THURSDAY))
        self.assertIn("is due tomorrow", drawer(100, self.THURSDAY))
        self.assertNotIn("is due", drawer(400, self.THURSDAY))



def trend(last7: float, avg: float, days: int) -> str:
    return render.trend_text(SimpleNamespace(last7=last7, prior_weekly_avg=avg, prior_days=days))


class TrendTest(unittest.TestCase):
    def test_up_down_or_the_same_as_shown(self):
        self.assertEqual(trend(120, 100, 28), ", up from a €100 weekly average over the previous 4 weeks")
        self.assertEqual(trend(80, 100, 28), ", down from a €100 weekly average over the previous 4 weeks")
        # Equal in whole euros, as the line shows them: never "up".
        self.assertEqual(trend(100.2, 99.8, 28), ", the same as a €100 weekly average over the previous 4 weeks")

    def test_a_short_history_says_how_short(self):
        self.assertEqual(trend(80, 70, 13), ", up from a €70 weekly average over the 13 days before")

    def test_no_comparison_under_a_week_of_history(self):
        self.assertEqual(trend(80, 0, 0), "")



class YenPaceTest(unittest.TestCase):
    """'Right on pace' within 25 euro-sized units: ¥3,750 for a yen household. Invented amounts."""

    def verdict(self, spent: float, scale: float) -> str:
        m = SimpleNamespace(fixed_paid=[], fixed_plan=[], fixed_remaining=[], pace_fraction=0.5,
                            day_to_day_budget=200_000, day_to_day_mtd=spent, today=date(2026, 10, 15),
                            one_offs_total=0)
        with mock.patch("butler.render.scale", return_value=scale), \
                mock.patch("butler.fmt._symbols", return_value=("¥", "JPY", "USD")):
            return render.split_lines(m)[1]

    def test_a_few_thousand_yen_is_right_on_pace(self):
        self.assertIn("(right on pace;", self.verdict(103_000, 150))
        self.assertIn("(¥4,000 over pace;", self.verdict(104_000, 150))
        self.assertIn("(¥3,000 over pace;", self.verdict(103_000, 1))



class CurrencyScaleTest(unittest.TestCase):
    """fmt.scale: euro-sized thresholds in the household's currency."""

    def scale(self, currency: dict) -> float:
        fmt.scale.cache_clear()
        self.addCleanup(fmt.scale.cache_clear)
        with mock.patch("butler.config.load_settings", return_value={"currency": currency}):
            return fmt.scale()

    def test_set_from_the_primary_currency(self):
        self.assertEqual(self.scale({"primary": "JPY"}), 150)
        self.assertEqual(self.scale({"primary": "sek"}), 10)
        self.assertEqual(self.scale({"primary": "EUR"}), 1)
        self.assertEqual(self.scale({"primary": "XYZ"}), 1)
        self.assertEqual(self.scale({}), 1)

    def test_a_setting_overrides_it(self):
        self.assertEqual(self.scale({"primary": "JPY", "scale": 100}), 100)



class ReadableTest(unittest.TestCase):
    """Accessibility: text colors reach 4.5:1 on the email's paper, and the email page has a
    language and a title."""

    def test_contrast_is_the_wcag_ratio(self):
        self.assertAlmostEqual(render.contrast("#ffffff", "#000000"), 21)
        self.assertAlmostEqual(render.contrast("#777777", "#777777"), 1)

    def test_a_light_person_color_is_darkened_to_4_5(self):
        flamingo = render.readable("#e67c73")
        self.assertNotEqual(flamingo, "#e67c73")
        self.assertGreaterEqual(render.contrast(flamingo, render.PAPER), 4.5)
        self.assertLess(render.contrast(flamingo, render.PAPER), 5.2)     # darkened only as far as needed

    def test_a_passing_color_is_kept(self):
        self.assertEqual(render.readable("#3F51B5"), "#3f51b5")
        self.assertEqual(render.readable("#d50000"), "#d50000")
        self.assertEqual(render.readable("rebeccapurple"), "rebeccapurple")

    def test_darkening_stops_at_black(self):
        self.assertEqual(render.readable("#ffffff", on="#000000"), "#ffffff")
        self.assertEqual(render.readable("#333333", on="#000000"), "#000000")

    def test_the_email_has_a_language_and_a_title(self):
        head = render.email_head("Bento · Monday 19 October <invented>")
        self.assertIn('<html lang="en">', head)
        self.assertIn("<title>Bento · Monday 19 October &lt;invented&gt;</title>", head)


if __name__ == "__main__":
    unittest.main()
