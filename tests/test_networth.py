"""Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests
Uses the example settings: EUR primary, USD second. All names and amounts are invented."""

import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

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


class SnapshotTest(unittest.TestCase):
    """Today's snapshot from Lunch Money's accounts, and comparisons with earlier ones."""

    MANUAL = {"manual_accounts": [
        {"name": "EUR account", "type": "cash", "currency": "eur", "balance": "3000", "to_base": 3000,
         "status": "active", "closed_on": None},
        {"name": "Old card", "type": "credit", "currency": "eur", "balance": "50", "to_base": 50,
         "status": "closed", "closed_on": "2026-01-01"},
        {"name": "Old wallet", "type": "cash", "currency": "eur", "balance": "5", "to_base": 5,
         "status": "closed", "closed_on": None},
        {"name": "Car loan", "type": "loan", "currency": "eur", "balance": "8000", "to_base": 8000,
         "status": "active", "closed_on": None}]}
    PLAID = {"plaid_accounts": [
        {"id": 1, "name": "Joint", "institution_name": "Broker", "mask": "1111", "type": "investment",
         "currency": "usd", "balance": "1000", "to_base": "860", "status": "active"},
        {"id": 2, "name": "Joint", "institution_name": "Broker", "type": "investment",
         "currency": "usd", "balance": "500", "to_base": "430", "status": "active"},
        {"id": 3, "name": "Savings", "type": "depository", "currency": "usd", "balance": "9",
         "to_base": "7.74", "status": "inactive"}]}

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        answers = {"manual_accounts": self.MANUAL, "plaid_accounts": self.PLAID}
        for p in (mock.patch.object(nw, "SNAPSHOTS", self.dir / "networth.jsonl"),
                  mock.patch.object(nw.lunchmoney, "get", side_effect=lambda path: answers[path])):
            p.start()
            self.addCleanup(p.stop)

    def test_open_accounts_only_debts_negative_and_same_names_told_apart(self):
        s = nw.snapshot(date(2026, 10, 9))
        self.assertEqual([(a["name"], a["balance"], a["value"]) for a in s["accounts"]], [
            ("EUR account", 3000, 3000), ("Car loan", -8000, -8000),
            ("Broker Joint ··1111", 1000, 860), ("Broker Joint ··2", 500, 430)])
        self.assertEqual(s["total"], -3710)

    def test_one_snapshot_a_day_kept_in_order(self):
        nw.snapshot(date(2026, 10, 8))
        nw.snapshot(date(2026, 10, 9))
        nw.snapshot(date(2026, 10, 9))                   # a second run replaces the day's first
        self.assertEqual([s["date"] for s in nw.load()], ["2026-10-08", "2026-10-09"])
        self.assertEqual(nw.previous(date(2026, 10, 9))["date"], "2026-10-08")
        self.assertIsNone(nw.previous(date(2026, 10, 8)))

    def test_change_since_compares_with_the_latest_old_enough_snapshot(self):
        for day in ("2026-09-01", "2026-09-30", "2026-10-05"):
            nw.snapshot(date.fromisoformat(day))
        now, ch = nw.change_since(date(2026, 10, 9), 7)
        self.assertEqual((now["date"], ch.previous), ("2026-10-09", -3710))
        with mock.patch.object(nw, "compare", side_effect=lambda a, b: b["date"]):
            self.assertEqual(nw.change_since(date(2026, 10, 9), 7)[1], "2026-09-30")
            self.assertEqual(nw.change_since(date(2026, 10, 9), 90)[1], "2026-09-01")   # none that old: the oldest

    def test_no_earlier_snapshot_means_no_change(self):
        now, ch = nw.change_since(date(2026, 10, 9), 7)
        self.assertEqual((now["date"], ch), ("2026-10-09", None))


class FxLineTest(unittest.TestCase):
    def test_the_rate_and_how_it_moved(self):
        before = snap("2026-10-08", 0.90, 100_000, 200_000, 3_000)
        cases = [(snap("2026-10-09", 0.9004, 100_000, 200_000, 3_000), None,
                  "Dollar: 1 USD = 0.9004 EUR (Lunch Money's rate)."),
                 (snap("2026-10-09", 0.9004, 100_000, 200_000, 3_000), before,
                  "Dollar: 1 USD = 0.9004 EUR (Lunch Money's rate), unchanged since 8 October."),
                 (snap("2026-10-09", 0.918, 100_000, 200_000, 3_000), before,
                  "Dollar: 1 USD = 0.9180 EUR (Lunch Money's rate), 2.0% up since the last brief: a stronger dollar."),
                 (snap("2026-10-09", 0.873, 100_000, 200_000, 3_000), before,
                  "Dollar: 1 USD = 0.8730 EUR (Lunch Money's rate), 3.0% down since the last brief: a stronger euro.")]
        for now, prev, want in cases:
            with self.subTest(want):
                self.assertEqual(nw.fx_line(now, prev), want)

    def test_no_second_currency_account_means_no_line(self):
        euros = {"date": "2026-10-09", "accounts": [{"name": "EUR account", "currency": "eur",
                                                     "balance": 10, "value": 10}]}
        self.assertEqual(nw.fx_line(euros, None), "")


if __name__ == "__main__":
    unittest.main()
