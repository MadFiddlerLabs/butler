"""Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests
All names and amounts are invented."""

import unittest
from datetime import date, datetime, timezone

from butler.budget import Line, Plan
from butler.finance import (CASH_TOPUP, DRAW, FUNDING, IGNORED, LEDGER, MANAGED_FEE, RENTAL, SPEND,
                          TRANSFER, classify, summarize)

RULES = {
    "map": {"Groceries": "Food", "Rent": "Housing", "Health": "Health", "Work": "EXCLUDE"},
    "overrides": [{"payee": "Corner Cafe", "category": "Food"},
                  {"payee": "Landlord", "category": "Housing", "label": "Rent", "next_month_from_day": 20},
                  {"payee": "Furniture Shop", "category": "Household", "one_off": True, "note": "new sofa"},
                  {"payee": "Employer Expenses", "category": "EXCLUDE"}],
    "cash_funding": [{"payee": "To Cash Account"}],
    "ledgers": [{"label": "Side project", "monthly_budget": 150, "payees": ["Hosting Co"],
                 "categories": ["Side Project"]},
                {"label": "Roof repair", "payees": ["Roofing Co"], "reimbursement_payees": ["Venmo"],
                 "reimbursements_from": "2026-09-01"}],
    "managed_fees": {"label": "Advisor fees", "payee": "ADVISORY FEE", "annual_rate": 0.01},
    "off_book": [{"item": "Babysitter", "paid": "from a separate wallet"}],
    "rules": {"ignore_accounts": ["IRA"], "ignore_statuses": ["delete_pending"],
              "rental_income_account": "Rental", "savings_account": "Savings",
              "savings_draw_payee": "To Everyday", "day_to_day": ["Food", "Household"]},
}

PLAN = Plan(fx_rate=0.86, lines=[
    Line("Food", "Groceries", "EUR", 900, "Monthly", None),
    Line("Housing", "Rent", "EUR", 1100, "Monthly", None),
    Line("Childcare", "Babysitter", "EUR", 800, "Monthly", None),
    Line("Housing", "Property tax", "USD", 1000, "Annual", 10),
])


def tx(id, day, payee, amount, account="Everyday", category="Groceries", **extra):
    return {"id": id, "date": day, "created_at": f"{day}T12:00:00Z", "payee": payee,
            "plaid_account_name": account, "to_base": str(amount), "category_name": category,
            "status": "cleared", **extra}


class ClassifyTest(unittest.TestCase):
    def kind(self, t):
        return classify(t, RULES).kind

    def test_kinds(self):
        self.assertEqual(self.kind(tx(1, "2026-10-02", "Grocer", 30)), SPEND)
        self.assertEqual(self.kind(tx(2, "2026-10-02", "Broker", 500, account="IRA")), IGNORED)
        self.assertEqual(self.kind(tx(3, "2026-10-02", "To Cash Account", 1000, exclude_from_totals=True)), FUNDING)
        self.assertEqual(self.kind(tx(4, "2026-10-02", "To Everyday", 3000, account="Savings",
                                      exclude_from_totals=True)), DRAW)
        self.assertEqual(self.kind(tx(5, "2026-10-02", "Tenant", -2000, account="Rental", category="Rent")), RENTAL)
        self.assertEqual(self.kind(tx(6, "2026-10-02", "Bank", 50, exclude_from_totals=True)), TRANSFER)
        self.assertEqual(self.kind(tx(7, "2026-09-30", "Monthly Interest Paid", -0.6, account="Rental")), "income")
        self.assertEqual(self.kind(tx(8, "2026-10-02", "ADVISORY FEE", 90, account="Brokerage")), MANAGED_FEE)

    def test_exclude_means_not_household(self):
        self.assertEqual(self.kind(tx(9, "2026-10-02", "Taxi", 20, category="Work")), IGNORED)
        self.assertEqual(self.kind(tx(10, "2026-10-02", "Employer Expenses", 80)), IGNORED)

    def test_ledgers_by_payee_category_and_reimbursement(self):
        for t, ledger in ((tx(11, "2026-10-02", "Hosting Co", 12), "Side project"),
                          (tx(12, "2026-10-02", "Domain", 15, category="Side Project"), "Side project"),
                          (tx(13, "2026-10-02", "Roofing Co", 900), "Roof repair"),
                          (tx(14, "2026-10-03", "Venmo", -300), "Roof repair")):
            c = classify(t, RULES)
            self.assertEqual((c.kind, c.ledger), (LEDGER, ledger))
        early = classify(tx(15, "2026-08-03", "Venmo", -300), RULES)   # before reimbursements_from
        self.assertNotEqual(early.kind, LEDGER)

    def test_overrides_beat_transfer_flag_and_bank_suffixes(self):
        lunch = classify(tx(16, "2026-10-02", "Corner Cafe (Pending)", 18, exclude_from_totals=True), RULES)
        self.assertEqual((lunch.kind, lunch.category, lunch.payee), (SPEND, "Food", "Corner Cafe"))

    def test_unmapped_goes_to_other(self):
        t = classify(tx(17, "2026-10-02", "Shop", 20, category="Mystery"), RULES)
        self.assertEqual((t.category, t.unmapped), ("Other", True))


class RentTimingTest(unittest.TestCase):
    def test_rent_paid_late_counts_for_next_month(self):
        late = classify(tx(20, "2026-09-29", "Landlord", 1100, category="Rent"), RULES)
        early = classify(tx(21, "2026-10-02", "Landlord", 1100, category="Rent"), RULES)
        self.assertEqual((late.payee, late.book_day), ("Rent", date(2026, 10, 1)))
        self.assertEqual(early.book_day, date(2026, 10, 2))
        s = summarize([late], PLAN, RULES, date(2026, 10, 9), datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.assertEqual(s.mtd_by_category, {"Housing": 1100})


class SummaryTest(unittest.TestCase):
    def test_pacing_trend_one_offs_and_off_book(self):
        raw = [
            tx(1, "2026-10-01", "Landlord", 1100, category="Rent"),       # fixed: never paced
            tx(2, "2026-10-05", "Grocer", 400),                            # daily
            tx(3, "2026-10-09", "Grocer", 100),
            tx(4, "2026-09-20", "Grocer", 400),                            # in the prior 4 weeks
            tx(5, "2026-10-06", "Furniture Shop", 700, category="Furniture"),   # one-off
        ]
        txs = [classify(t, RULES) for t in raw]
        since = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)
        s = summarize(txs, PLAN, RULES, date(2026, 10, 10), since)

        self.assertEqual(s.budget_month, 2000)          # the babysitter is off-book
        self.assertEqual(s.mtd_total, 2300)
        self.assertEqual(s.day_to_day_mtd, 500)         # the one-off stays out of daily
        self.assertEqual(s.one_offs_total, 700)
        self.assertAlmostEqual(s.pace_fraction, 10 / 31)
        # Food: 500 vs 900 * 10/31 = 290 -> over by 210 (> max(100, 72))
        self.assertEqual([c for c, _, _ in s.over_pace], ["Food"])
        self.assertEqual(s.last7, 500)                  # 4–10 Oct, one-off excluded
        self.assertEqual(s.prior_weekly_avg, 200)       # 400 over the 2 weeks the history covers (from 20 Sep)
        self.assertEqual(s.prior_days, 14)
        self.assertEqual([t.id for t in s.new], [3])
        self.assertEqual(s.lumps_due, [("Property tax", 860.0)])
        self.assertEqual(s.off_book[0][:2], ("Babysitter", 800.0))
        self.assertEqual(s.fixed_paid, [("Rent", 1100)])

    def test_ledger_summary(self):
        raw = [tx(1, "2026-10-02", "Hosting Co", 40), tx(2, "2026-10-03", "Roofing Co", 900),
               tx(3, "2026-10-04", "Venmo", -300)]
        s = summarize([classify(t, RULES) for t in raw], PLAN, RULES, date(2026, 10, 10),
                      datetime(2026, 10, 9, tzinfo=timezone.utc))
        by = {l.label: l for l in s.ledgers}
        self.assertEqual((by["Side project"].month, by["Side project"].budget), (40, 150))
        self.assertEqual((by["Roof repair"].costs, by["Roof repair"].reimbursed), (900, 300))
        self.assertEqual(s.mtd_total, 0)                # ledgers stay out of household spending


class CashDrawerTest(unittest.TestCase):
    RULES = {**RULES, "cash_drawer": {
        "topup_payee": "ATM", "account": "Everyday", "topup_person": "Alex",
        "recurring": {"label": "Nanny (cash)", "amount": 180, "weekday": "friday", "category": "Childcare",
                      "budget_line": "Babysitter", "since": "2026-09-04"},
        "count": {"date": "2026-10-02", "amount": 40}}, "off_book": []}

    def test_topups_and_recurring_payments(self):
        raw = [tx(1, "2026-10-08", "ATM", 300)]
        txs = [classify(t, self.RULES) for t in raw]
        self.assertEqual(txs[0].kind, CASH_TOPUP)
        s = summarize(txs, PLAN, self.RULES, date(2026, 10, 10), datetime(2026, 10, 9, tzinfo=timezone.utc))
        # Paydays this month: 2, 9, 16, 23, 30 Oct -> planned 900; paid so far: 2 and 9 Oct.
        self.assertEqual(s.budget_by_category["Childcare"], 900)
        self.assertEqual(s.mtd_by_category["Childcare"], 360)
        # Drawer: 40 counted on 2 Oct, + 300 top-up, − 180 on 9 Oct.
        self.assertEqual((s.drawer, s.drawer_counted), (160, date(2026, 10, 2)))
        self.assertEqual(s.cash_topups_mtd, 300)


if __name__ == "__main__":
    unittest.main()
