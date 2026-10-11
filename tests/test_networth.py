"""Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests
Uses the example settings: EUR primary, USD second. All names and amounts are invented."""

import unittest

from butler import networth as nw


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

    def test_an_account_that_drops_out_is_explained(self):
        """A closed account, or a bank link that broke overnight: the whole change is accounted for."""
        before = snap("2026-10-08", 0.90, 100_000, 200_000, 3_000)
        now = {**before, "date": "2026-10-09", "accounts": before["accounts"][1:]}
        now["total"] = sum(a["value"] for a in now["accounts"])
        ch = nw.compare(now, before)
        self.assertEqual([(m.account, m.kind, round(m.real)) for m in ch.moves],
                         [("Joint Savings", "not in Lunch Money today", -90_000)])
        self.assertAlmostEqual(ch.total - ch.previous, ch.fx + sum(m.real for m in ch.moves))
        text = nw.line(now, ch, "since yesterday", 1000, reasons={"Joint Savings": "spending"})
        self.assertIn("Joint Savings −€90,000 (not in Lunch Money today)", text)

    def test_a_negative_net_worth_reads_naturally_in_both_currencies(self):
        debt = {"date": "2026-10-09", "total": -8_600.0, "accounts": [
            {"name": "Card", "type": "credit", "currency": "usd", "balance": -10_000, "value": -8_600.0},
            {"name": "Joint Savings", "type": "depository", "currency": "usd", "balance": 1, "value": 0.86}]}
        self.assertIn("Net worth: −€8,600 (−$10,000)", nw.line(debt, None, "since yesterday", 1000))


if __name__ == "__main__":
    unittest.main()
