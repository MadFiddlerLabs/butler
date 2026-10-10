"""Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests
Uses the example settings: EUR primary, USD second. All names and amounts are invented."""

import unittest

from casa import networth as nw


def snap(day, usd_rate, savings_usd, invest_usd, eur_cash):
    accts = [
        {"name": "Joint Savings", "type": "depository", "currency": "usd",
         "balance": savings_usd, "value": savings_usd * usd_rate},
        {"name": "Brokerage Joint", "type": "investment", "currency": "usd",
         "balance": invest_usd, "value": invest_usd * usd_rate},
        {"name": "EUR account", "type": "depository", "currency": "eur", "balance": eur_cash, "value": eur_cash},
    ]
    return {"date": day, "total": sum(a["value"] for a in accts), "accounts": accts}


class NetWorthTest(unittest.TestCase):
    def test_fx_and_real_moves_are_split(self):
        before = snap("2026-10-08", 0.90, 100_000, 200_000, 3_000)
        now = snap("2026-10-09", 0.89, 96_620, 202_000, 6_000)   # $3,380 moved, +$2,000 markets
        ch = nw.compare(now, before)
        self.assertAlmostEqual(ch.fx, 300_000 * (0.89 - 0.90))           # old USD at the new rate
        real = {m.account: round(m.real) for m in ch.moves}
        self.assertEqual(real, {"Joint Savings": round(-3_380 * 0.89),
                                "Brokerage Joint": round(2_000 * 0.89), "EUR account": 3_000})
        self.assertAlmostEqual(ch.total - ch.previous, ch.fx + sum(m.real for m in ch.moves), places=6)

    def test_line_explains_only_notable_changes(self):
        before = snap("2026-10-08", 0.90, 100_000, 200_000, 3_000)
        steady = snap("2026-10-09", 0.90, 100_000, 200_500, 3_000)
        quiet = nw.line(steady, nw.compare(steady, before), "since yesterday", 2500)
        self.assertNotIn("What changed", quiet)
        loud_now = snap("2026-10-09", 0.89, 96_620, 202_000, 6_000)
        loud = nw.line(loud_now, nw.compare(loud_now, before), "since yesterday", 1000,
                       {"Brokerage Joint": "Brokerage"}, {"Joint Savings": "drawn from savings for spending"})
        self.assertIn("the dollar weakened 1.1% against the euro", loud)
        self.assertIn("Joint Savings −€3,008 (drawn from savings for spending)", loud)
        self.assertIn("($", loud)                                         # total shown in USD too


if __name__ == "__main__":
    unittest.main()
