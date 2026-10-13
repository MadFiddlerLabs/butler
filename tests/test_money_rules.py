"""The money rules one at a time: each test pins a rule in finance.py that mutation testing showed
no other test held (a setting read, a threshold, a window, a sort order). test_finance.py has the
examples, test_money_properties.py the invariants. All payees, people and amounts are invented.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import yaml

from butler import charts, finance, weekly
from butler.budget import Line, Plan
from butler.config import config_path
from butler.finance import (CASH_TOPUP, DRAW, IGNORED, LEDGER, MANAGED_FEE, SPEND, TRANSFER, Tx,
                            classify, subscriptions, summarize)

UTC = timezone.utc
TODAY = date(2026, 10, 20)                      # a Tuesday
SINCE = datetime(2026, 10, 19, 6, tzinfo=UTC)   # the last brief


def raw(**kw) -> dict:
    """A Lunch Money transaction, as the API sends it."""
    return {"id": 1, "date": "2026-10-02", "created_at": "2026-10-02T12:00:00Z", "payee": "Grocer",
            "to_base": "30", "category_name": "Groceries", "status": "cleared", **kw}


def spend(id, day, payee, amount, category="Food", kind=SPEND, created=None, **kw) -> Tx:
    return Tx(id=id, day=day, created=created or datetime.combine(day, datetime.min.time(), UTC),
              payee=payee, account="Everyday", amount=amount, kind=kind, category=category, **kw)


BASE_RULES = {"map": {"Groceries": "Food"}, "rules": {"day_to_day": ["Food"]}}

# One line per rule the fixed plan follows. No line is in the second currency, so amounts are as written.
PLAN = Plan(fx_rate=0.86, lines=[
    Line("Housing", "Rent", None, 1100, "Monthly", None),
    Line("Housing", "Parking", None, 100, "Monthly", None),          # netted from rent: off the plan
    Line("Housing", "Property tax", None, 1000, "Annual", 10),      # a lump, not monthly
    Line("Utilities", "Electricity", None, 80, "Monthly", None),    # two lines: named after the category
    Line("Utilities", "Water", None, 40, "Monthly", None),
    Line("Food", "Groceries", None, 900, "Monthly", None),          # daily spending, paced
    Line("Food", "Swim snacks", None, 60, "Monthly", None),         # fixed lines inside a daily category
    Line("Food", "School lunch", None, 90, "Monthly", None),
    Line("Childcare", "Babysitter", None, 800, "Monthly", None),    # off-book
    Line("Storage", "Locker", None, 0, "Monthly", None),            # nothing planned: left out
    Line("Pets", "Fish food", None, 1, "Monthly", None),            # the smallest plan still counts
])
PLAN_RULES = {
    "map": {}, "off_book": [{"item": "Babysitter", "paid": "from a separate wallet"}],
    "rules": {"day_to_day": ["Food"], "fixed_lines": ["Swim snacks", "School lunch"],
              "netted_from_rent": ["Parking"]},
}


class ClassifyReadsEveryField(unittest.TestCase):
    RULES = {**BASE_RULES, "overrides": [{"payee": "Corner Cafe", "account": "Joint", "category": "Food",
                                          "note": "team lunches", "label": "Cafe"}],
             "rules": {"day_to_day": ["Food"], "ignore_institutions": ["Old Bank"]}}

    def test_a_manual_account_is_named_by_asset_name(self):
        t = classify(raw(payee="Corner Cafe", asset_name="Joint"), self.RULES)
        self.assertEqual((t.account, t.kind, t.category), ("Joint", SPEND, "Food"))

    def test_an_override_with_an_account_matches_only_that_account(self):
        here = classify(raw(payee="Corner Cafe", plaid_account_name="Joint"), self.RULES)
        elsewhere = classify(raw(payee="Corner Cafe", plaid_account_name="Everyday", category_name="Cafes"),
                             self.RULES)
        nowhere = classify(raw(payee="Corner Cafe", category_name="Cafes"), self.RULES)
        self.assertEqual((here.category, here.note, here.payee), ("Food", "team lunches", "Cafe"))
        self.assertEqual((elsewhere.category, elsewhere.note, elsewhere.payee), ("Other", "", "Corner Cafe"))
        self.assertEqual((nowhere.account, nowhere.category), ("", "Other"))

    def test_an_override_without_a_note_or_flags_leaves_them_off(self):
        rules = {**BASE_RULES, "overrides": [{"payee": "Grocer", "category": "Food"}]}
        t = classify(raw(), rules)
        self.assertEqual((t.note, t.one_off, t.fixed, t.line), ("", False, False, ""))
        self.assertIs(t.one_off, False)

    def test_an_ignored_institution_is_ignored(self):
        self.assertEqual(classify(raw(institution_name="Old Bank"), self.RULES).kind, IGNORED)
        self.assertEqual(classify(raw(institution_name="New Bank"), self.RULES).kind, SPEND)

    def test_missing_fields_have_safe_defaults(self):
        t = classify({k: v for k, v in raw().items() if k not in ("payee", "to_base")}, self.RULES)
        self.assertEqual((t.payee, t.amount, t.recurring_id), ("", 0.0, None))

    def test_the_recurring_id_and_creation_time_are_kept(self):
        t = classify(raw(recurring_id=77, created_at="2026-10-02T12:30:00Z"), self.RULES)
        self.assertEqual(t.recurring_id, 77)
        self.assertEqual(t.created, datetime(2026, 10, 2, 12, 30, tzinfo=UTC))

    def test_every_classifying_setting_is_read(self):
        rules = {**BASE_RULES,
                 "overrides": [{"payee": "Swim School", "category": "Food", "fixed": True, "line": "Swim snacks"}],
                 "cash_funding": [{"payee": "To Cash", "account": "Everyday"}],
                 "ledgers": [{"label": "Roof", "reimbursement_payees": ["Venmo"], "reimbursements_from": "2026-09-01"}],
                 "rules": {"day_to_day": ["Food"], "ignore_statuses": ["delete_pending"], "savings_account": "Savings",
                           "savings_draw_payee": "To Everyday", "rental_income_account": "Rental"}}
        swim = classify(raw(payee="Swim School"), rules)
        self.assertEqual((swim.fixed, swim.line), (True, "Swim snacks"))
        cases = [
            ("a status to ignore", raw(status="delete_pending"), IGNORED),
            ("cash funding from its account", raw(payee="To Cash", plaid_account_name="Everyday",
                                                 exclude_from_totals=True), finance.FUNDING),
            ("cash funding from another account", raw(payee="To Cash", plaid_account_name="Joint",
                                                     exclude_from_totals=True), TRANSFER),
            ("money back from the first day", raw(payee="Venmo", to_base="-300", date="2026-09-01"), LEDGER),
            ("money back the day before", raw(payee="Venmo", to_base="-300", date="2026-08-31"), SPEND),
            ("a draw from savings", raw(payee="To Everyday", plaid_account_name="Savings", to_base="3000",
                                       exclude_from_totals=True), DRAW),
            ("savings moved elsewhere", raw(payee="To Brokerage", plaid_account_name="Savings", to_base="3000",
                                           exclude_from_totals=True), TRANSFER),
            ("rent received", raw(payee="Tenant", plaid_account_name="Rental", to_base="-2000"), finance.RENTAL),
            ("a transfer on the rental account", raw(payee="Tenant", plaid_account_name="Rental", to_base="-2000",
                                                    exclude_from_totals=True), TRANSFER),
            ("a refund elsewhere", raw(payee="Shop", to_base="-20"), SPEND),
            ("pay", raw(payee="Employer", to_base="-4000", is_income=True), finance.INCOME),
            ("interest charged", raw(payee="Interest charge", to_base="0.75"), SPEND),
        ]
        for name, t, kind in cases:
            with self.subTest(name):
                self.assertEqual(classify(t, rules).kind, kind)

    def test_without_a_draw_payee_anything_leaving_savings_is_a_draw(self):
        rules = {**BASE_RULES, "rules": {"day_to_day": ["Food"], "savings_account": "Savings"}}
        t = classify(raw(payee="To Brokerage", plaid_account_name="Savings", to_base="3000",
                         exclude_from_totals=True), rules)
        self.assertEqual(t.kind, DRAW)

    def test_the_rules_come_from_the_category_map(self):
        with open(config_path("category_map")) as f:
            self.assertEqual(finance.load_rules(), yaml.safe_load(f))


class FixedPlan(unittest.TestCase):
    def summary(self, txs=(), today=TODAY):
        return summarize(list(txs), PLAN, PLAN_RULES, today, SINCE)

    def test_the_plan_names_groups_and_orders_fixed_expenses(self):
        self.assertEqual(self.summary().fixed_plan, [
            ("Rent", 1100),          # one line: its own name; Parking is netted from rent
            ("Utilities", 120),      # two lines: the category's name
            ("School lunch", 90),    # each fixed line on its own, though Food is daily
            ("Swim snacks", 60),
            ("Fish food", 1),        # the locker plans nothing, so it's left out
        ])

    def test_budgets_leave_out_off_book_and_netted_lines_and_daily_leaves_out_fixed_ones(self):
        s = self.summary()
        self.assertEqual(s.budget_by_category, {"Housing": 1100, "Utilities": 120, "Food": 1050,
                                                "Storage": 0, "Pets": 1})
        self.assertEqual(s.budget_month, 2271)
        self.assertEqual((s.daily_budget_by_category, s.day_to_day_budget), ({"Food": 900}, 900))

    def test_still_to_come_and_paid(self):
        txs = [spend(1, date(2026, 10, 1), "Landlord", 1100, "Housing"),
               spend(2, date(2026, 10, 3), "Power Co", 80, "Utilities"),
               spend(3, date(2026, 10, 5), "School", 75, fixed=True, line="School lunch"),     # 15 left
               spend(4, date(2026, 10, 6), "Swim Club", 45.01, fixed=True, line="Swim snacks"),  # 14.99 left
               spend(5, date(2026, 10, 7), "Pet Shop", 1, "Pets"),
               spend(6, date(2026, 10, 8), "Grocer", 200, line="Swim snacks")]   # daily, not marked fixed
        s = self.summary(txs)
        # Gaps of 15 or more are still to come; smaller ones are bills that came in under plan.
        self.assertEqual(s.fixed_remaining, [("Utilities", 40), ("School lunch", 15)])
        self.assertEqual(s.fixed_paid, [("Rent", 1100), ("Utilities", 80), ("School lunch", 75),
                                        ("Swim snacks", 45.01), ("Fish food", 1)])

    def test_with_nothing_paid_everything_is_still_to_come(self):
        s = self.summary()
        self.assertEqual(s.fixed_remaining, [("Rent", 1100), ("Utilities", 120), ("School lunch", 90),
                                             ("Swim snacks", 60)])
        self.assertEqual((s.fixed_paid, s.bills_mtd), ([], []))

    def test_bills_by_payee_biggest_first(self):
        txs = [spend(1, date(2026, 10, 3), "Aqua Co", 30, "Utilities"),
               spend(2, date(2026, 10, 1), "Landlord", 1100, "Housing"),
               spend(3, date(2026, 10, 5), "School", 75, fixed=True, line="School lunch"),
               spend(4, date(2026, 10, 9), "Aqua Co", 50, "Utilities"),
               spend(5, date(2026, 10, 9), "Grocer", 400)]                    # daily, not a bill
        self.assertEqual(self.summary(txs).bills_mtd, [("Landlord", 1100), ("Aqua Co", 80), ("School", 75)])

    def test_off_book_lines_say_how_they_are_paid(self):
        self.assertEqual(self.summary().off_book, [("Babysitter", 800, "from a separate wallet")])
        # In the second currency, at the plan's rate; and with no `paid`, nothing said.
        plan = Plan(0.86, [Line("Childcare", "Babysitter", "USD", 800, "Monthly", None)])
        rules = {**PLAN_RULES, "off_book": [{"item": "Babysitter"}]}
        self.assertEqual(summarize([], plan, rules, TODAY, SINCE).off_book, [("Babysitter", 688, "")])

    def test_a_payment_under_one_is_not_listed_as_paid(self):
        s = self.summary([spend(1, date(2026, 10, 7), "Pet Shop", 0.99, "Pets")])
        self.assertNotIn("Fish food", dict(s.fixed_paid))


class RecurringCash(unittest.TestCase):
    """A weekly cash payment from the drawer, on the configured weekday, from its start date."""

    def rules(self, weekday):
        return {**PLAN_RULES, "off_book": [], "cash_drawer": {
            "topup_payee": "ATM", "account": "Everyday",
            "recurring": {"label": "Nanny (cash)", "amount": 100, "weekday": weekday, "category": "Childcare",
                          "budget_line": "Babysitter", "since": "2026-10-09"}}}

    def test_the_plan_counts_paydays_from_the_start_date_to_the_month_end(self):
        # October 2026, from Friday 9: Sundays 11, 18, 25; Saturdays 10, 17, 24, 31.
        for weekday, paydays, paid in (("Sunday", 3, [date(2026, 10, 11), date(2026, 10, 18)]),
                                       ("saturday", 4, [date(2026, 10, 10), date(2026, 10, 17)])):
            with self.subTest(weekday):
                s = summarize([], PLAN, self.rules(weekday), TODAY, SINCE)
                self.assertEqual(s.budget_by_category["Childcare"], 100 * paydays)
                self.assertIn(("Babysitter", 100 * paydays), s.fixed_plan)
                self.assertIn(("Rent", 1100), s.fixed_plan)            # only the cash line changes
                cash = [t for t in s.txs if t.payee == "Nanny (cash)"]
                self.assertEqual([t.day for t in cash], paid)
                self.assertEqual(s.fixed_paid, [("Babysitter", 200)])

    def test_the_payments_look_like_spending_from_the_drawer(self):
        rules = self.rules("friday")
        rules["cash_drawer"]["recurring"].pop("weekday")      # Friday is the default
        t = finance._recurring_cash(rules, date(2026, 10, 1), date(2026, 10, 9))
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0].id, t[0].day, t[0].account, t[0].amount, t[0].kind, t[0].category,
                          t[0].fixed, t[0].line),
                         (-20261009, date(2026, 10, 9), "cash drawer", 100.0, SPEND, "Childcare",
                          True, "Babysitter"))
        # The plan counts Fridays too: 9, 16, 23 and 30 October.
        self.assertEqual(summarize([], PLAN, rules, TODAY, SINCE).budget_by_category["Childcare"], 400)

    def test_without_a_start_date_it_runs_from_the_start(self):
        rules = self.rules("friday")
        rules["cash_drawer"]["recurring"].pop("since")
        days = [t.day for t in finance._recurring_cash(rules, date(2026, 10, 1), date(2026, 10, 20))]
        self.assertEqual(days, [date(2026, 10, 2), date(2026, 10, 9), date(2026, 10, 16)])
        s = summarize([], PLAN, rules, TODAY, SINCE)
        self.assertEqual(s.budget_by_category["Childcare"], 500)   # five Fridays in October 2026

    def test_without_a_budget_line_the_payments_are_on_no_line(self):
        rules = self.rules("friday")
        rules["cash_drawer"]["recurring"].pop("budget_line")
        self.assertEqual({t.line for t in finance._recurring_cash(rules, date(2026, 10, 1), TODAY)}, {""})

    def test_the_drawer_estimate_takes_off_only_the_recurring_payments(self):
        rules = self.rules("friday")
        rules["cash_drawer"]["count"] = {"date": "2026-10-12", "amount": 500}
        txs = [spend(1, date(2026, 10, 14), "ATM", 200, kind=CASH_TOPUP),
               spend(2, date(2026, 10, 15), "Market stall", 30)]    # other cash spending isn't known
        s = summarize(txs, PLAN, rules, TODAY, SINCE)
        # 500 counted on 12 October, + 200, − the nanny on Friday 16 October.
        self.assertEqual((s.drawer, s.drawer_counted), (600, date(2026, 10, 12)))

    def test_weekdays(self):
        thursday = date(2026, 10, 1)
        self.assertEqual(finance._weekdays(thursday, date(2026, 10, 31), 0),    # Mondays
                         [date(2026, 10, 5), date(2026, 10, 12), date(2026, 10, 19), date(2026, 10, 26)])
        self.assertEqual(finance._weekdays(thursday, thursday, 3), [thursday])
        self.assertEqual(finance._weekdays(thursday, date(2026, 10, 4), 4), [date(2026, 10, 2)])


class SummaryWindows(unittest.TestCase):
    RULES = {**PLAN_RULES, "managed_fees": {"payee": "ADVISORY FEE", "annual_rate": 0.01,
                                            "managed_institution": "Fidelis"},
             "rules": {**PLAN_RULES["rules"], "savings_account": "Savings"},
             "cash_drawer": {"topup_payee": "ATM", "account": "Everyday", "recurring": {
                 "label": "Nanny (cash)", "amount": 100, "weekday": "friday", "category": "Childcare",
                 "since": "2026-10-16"}}}

    def setUp(self):
        # Never the household's own balance history (data/networth.jsonl).
        patcher = mock.patch("butler.networth.load", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_new_means_arrived_since_the_last_brief_and_dated_in_the_last_week(self):
        after = SINCE + timedelta(hours=1)
        txs = [spend(1, date(2026, 10, 12), "Grocer", 20, created=after),     # seven days before: new
               spend(2, date(2026, 10, 11), "Grocer", 90, created=after),     # imported history
               spend(3, date(2026, 10, 19), "Grocer", 50, created=SINCE),     # in the last brief
               spend(4, date(2026, 10, 19), "Bakery", 30, created=after),
               spend(5, date(2026, 10, 19), "Hardware", 5, "Household", created=after)]
        s = summarize(txs, PLAN, PLAN_RULES, TODAY, SINCE)
        self.assertEqual([t.id for t in s.new], [4, 1, 5])
        self.assertEqual(s.new_by_category, {"Food": 50, "Household": 5})
        # A new one-off or fixed line is new, but it isn't new daily spending in the chart.
        txs += [spend(6, date(2026, 10, 19), "Sofa Shop", 700, created=after, one_off=True),
                spend(7, date(2026, 10, 19), "School", 90, created=after, fixed=True)]
        s = summarize(txs, PLAN, PLAN_RULES, TODAY, SINCE)
        self.assertEqual([t.id for t in s.new], [6, 7, 4, 1, 5])
        self.assertEqual(s.new_by_category, {"Food": 50, "Household": 5})
        self.assertEqual((s.today, s.since), (TODAY, SINCE))

    def test_over_pace(self):
        # 20 of 31 days gone. A daily category is over pace when it's more than 100 over what the
        # month so far expects, and more than a quarter over.
        rules = {**PLAN_RULES, "rules": {**PLAN_RULES["rules"], "day_to_day": ["Food", "Cinema", "Big", "Garden"]}}
        plan = Plan(1, PLAN.lines + [Line("Cinema", "Outings", None, 400, "Monthly", None),
                                     Line("Big", "Groceries for six", None, 1000, "Monthly", None)])
        txs = [spend(2, date(2026, 10, 2), "Cinema", 358.57, "Cinema"),  # expects 258.06: 100.51 over
               spend(1, date(2026, 10, 2), "Grocer", 900),            # expects 580.65: 319.35 over: listed first
               spend(3, date(2026, 10, 3), "Market", 765.16, "Big"),  # expects 645.16: 120 over, under a quarter
               spend(4, date(2026, 10, 3), "Nursery", 170, "Garden")]  # nothing planned, so no pace
        s = summarize(txs, plan, rules, TODAY, SINCE)
        self.assertEqual([(c, round(a, 2), round(e, 2)) for c, a, e in s.over_pace],
                         [("Food", 900, 580.65), ("Cinema", 358.57, 258.06)])

    def test_one_offs_stay_out_of_every_category_compared_with_a_budget(self):
        # Invented: an 800 bookcase as a one-off in Food, whose daily budget is 900.
        txs = [spend(1, date(2026, 10, 2), "Grocer", 100),
               spend(2, date(2026, 10, 3), "Bookcase Shop", 800, one_off=True),
               spend(3, date(2026, 10, 4), "Gift Shop", 400, "Gifts", one_off=True),  # no budget line
               spend(4, date(2026, 10, 5), "School", 90, fixed=True, line="School lunch")]
        s = summarize(txs, PLAN, PLAN_RULES, TODAY, SINCE)
        self.assertEqual(s.mtd_by_category, {"Food": 990, "Gifts": 400})
        self.assertEqual(s.against_budget_by_category, {"Food": 190})
        self.assertEqual(s.unbudgeted, [])          # a one-off is named, not asked to get a budget line
        self.assertEqual(charts.category_bars(s, ["Food"]), [("Food", 100, 900)])
        rules = {"rules": {"day_to_day": ["Food"]}}
        self.assertEqual(weekly._category_rows(s, rules), ([], ["Food", "Housing", "Pets", "Utilities"]))

    def test_unbudgeted_means_over_50_with_nothing_planned(self):
        txs = [spend(1, date(2026, 10, 4), "Gift Shop", 51, "Gifts"),
               spend(2, date(2026, 10, 4), "Hobby Shop", 50, "Hobbies"),   # 50: too small to mention
               spend(3, date(2026, 10, 5), "Storage Co", 80, "Storage"),   # a plan of 0 is no plan
               spend(4, date(2026, 10, 5), "Pet Shop", 70, "Pets")]        # planned, though only 1
        s = summarize(txs, PLAN, PLAN_RULES, TODAY, SINCE)
        self.assertEqual(s.unbudgeted, [("Storage", 80), ("Gifts", 51)])

    def test_the_month_to_date_sums_count_the_first_and_today(self):
        txs = [spend(1, date(2026, 9, 30), "To Everyday", 1000, kind=DRAW),
               spend(2, date(2026, 10, 1), "To Everyday", 60, kind=DRAW),
               spend(3, TODAY, "To Everyday", 40, kind=DRAW),
               spend(4, date(2026, 10, 2), "ADVISORY FEE", 50, kind=MANAGED_FEE),
               spend(5, date(2026, 10, 2), "Bank", 500, kind=TRANSFER),
               spend(6, date(2026, 10, 3), "To Cash", 250, kind=finance.FUNDING),
               spend(7, date(2026, 10, 4), "Tenant", -900, kind=finance.RENTAL)]
        s = summarize(txs, PLAN, self.RULES, TODAY, SINCE)
        self.assertEqual((s.draw_mtd, s.managed_fees_mtd, s.funding_mtd, s.rental_mtd), (100, 50, 250, 900))
        self.assertEqual((s.managed_fees_label, s.managed_fee_rate), ("Advisor fees", 0.01))
        named = {**self.RULES, "managed_fees": {"payee": "PLANNER", "label": "Planner fees"}}
        s = summarize([], PLAN, named, TODAY, SINCE)
        self.assertEqual((s.managed_fees_label, s.managed_fee_rate), ("Planner fees", 0.0))

    def test_the_savings_draw_is_a_three_month_average(self):
        txs = [spend(1, TODAY - timedelta(days=90), "To Everyday", 900, kind=DRAW),   # outside the window
               spend(2, TODAY - timedelta(days=89), "To Everyday", 300, kind=DRAW),
               spend(3, date(2026, 9, 1), "To Everyday", 600, kind=DRAW),
               spend(4, date(2026, 9, 2), "Grocer", 1000),
               spend(5, TODAY, "To Everyday", 30, kind=DRAW)]
        s = summarize(txs, PLAN, self.RULES, TODAY, SINCE)
        self.assertEqual(s.draw_monthly, 310)

    def test_a_first_draw_divides_by_the_days_since_it(self):
        # Draws that began 60 days ago: two months of them, not three.
        txs = [spend(1, TODAY - timedelta(days=59), "To Everyday", 400, kind=DRAW),
               spend(2, TODAY - timedelta(days=29), "To Everyday", 400, kind=DRAW)]
        self.assertEqual(summarize(txs, PLAN, self.RULES, TODAY, SINCE).draw_monthly, 400)

    def test_runway_waits_for_a_full_month_of_draws(self):
        month = [spend(1, TODAY - timedelta(days=29), "To Everyday", 500, kind=DRAW)]   # 30 days
        short = [spend(1, TODAY - timedelta(days=28), "To Everyday", 500, kind=DRAW)]   # 29 days
        self.assertEqual(summarize(month, PLAN, self.RULES, TODAY, SINCE).draw_monthly, 500)
        self.assertEqual(summarize(short, PLAN, self.RULES, TODAY, SINCE).draw_monthly, 0)
        self.assertEqual(summarize([], PLAN, self.RULES, TODAY, SINCE).draw_monthly, 0)

    def test_cash_over_the_last_eight_weeks(self):
        txs = [spend(1, TODAY - timedelta(days=56), "ATM", 400, kind=CASH_TOPUP),   # outside
               spend(2, TODAY - timedelta(days=55), "ATM", 200, kind=CASH_TOPUP),
               spend(3, date(2026, 10, 1), "ATM", 100, kind=CASH_TOPUP),
               spend(4, TODAY, "ATM", 1, kind=CASH_TOPUP),
               # Payments to the nanny typed into Lunch Money count too, on the window's edges.
               spend(5, TODAY - timedelta(days=56), "Nanny (cash)", 1000),
               spend(6, TODAY - timedelta(days=55), "Nanny (cash)", 7),
               spend(7, TODAY, "Nanny (cash)", 3)]
        s = summarize(txs, PLAN, self.RULES, TODAY, SINCE)
        self.assertEqual((s.cash_topups_8w, s.cash_topups_mtd), (301, 101))
        # The recurring payment runs from 16 October: one Friday so far.
        self.assertEqual((s.cash_payments_8w, s.cash_payment_label), (110, "Nanny (cash)"))
        s = summarize(txs, PLAN, PLAN_RULES, TODAY, SINCE)          # no cash drawer at all
        self.assertEqual((s.cash_payments_8w, s.cash_payment_label), (0, ""))

    def test_balances_come_from_the_latest_snapshot(self):
        def snap(managed, other, savings):
            return {"accounts": [{"name": "Fidelis Brokerage", "value": managed},
                                 {"name": "Fidelis IRA", "value": other},
                                 {"name": "Everyday", "value": 200},
                                 {"name": "Joint Savings", "value": savings}]}
        snaps = [snap(1, 1, 1), snap(2, 2, 2), snap(1000, 500, 3000)]
        with mock.patch("butler.networth.load", return_value=snaps):
            s = summarize([], PLAN, self.RULES, TODAY, SINCE)
        self.assertEqual((s.managed_balance, s.savings_balance), (1500, 3000))
        unmanaged = {**self.RULES, "managed_fees": {"payee": "ADVISORY FEE"}}
        with mock.patch("butler.networth.load", return_value=snaps):
            self.assertEqual(summarize([], PLAN, unmanaged, TODAY, SINCE).managed_balance, 0.0)
        with mock.patch("butler.networth.load", return_value=[]):
            s = summarize([], PLAN, self.RULES, TODAY, SINCE)
        self.assertEqual((s.managed_balance, s.savings_balance), (0.0, None))

    def test_the_last_seven_days_against_the_four_weeks_before(self):
        txs = [spend(1, date(2026, 10, 14), "Grocer", 10),        # the first of the last seven days
               spend(2, TODAY, "Grocer", 20),
               spend(3, date(2026, 10, 13), "Grocer", 100),       # the four weeks before: 16 Sep to 13 Oct
               spend(4, date(2026, 9, 16), "Grocer", 200),
               spend(5, date(2026, 9, 15), "Grocer", 1000)]       # too early for either
        s = summarize(txs, PLAN, PLAN_RULES, TODAY, SINCE)
        self.assertEqual((s.last7, s.prior_weekly_avg), (30, 75))

    def test_a_short_history_averages_over_the_days_it_covers(self):
        # Daily spending began on 1 October: 13 days before the last seven, so 130 is 70 a week.
        txs = [spend(1, date(2026, 10, 1), "Grocer", 60),
               spend(2, date(2026, 10, 13), "Grocer", 70),
               spend(3, date(2026, 9, 1), "Sofa Shop", 900, one_off=True),     # one-offs don't start it
               spend(4, date(2026, 9, 1), "School", 90, fixed=True),           # nor fixed lines
               spend(5, TODAY, "Grocer", 20)]
        s = summarize(txs, PLAN, PLAN_RULES, TODAY, SINCE)
        self.assertEqual((s.prior_weekly_avg, s.prior_days), (70, 13))

    def test_under_a_week_of_history_has_no_weekly_average(self):
        week = [spend(1, date(2026, 10, 7), "Grocer", 40), spend(2, TODAY, "Grocer", 20)]
        days = [spend(1, date(2026, 10, 8), "Grocer", 40), spend(2, TODAY, "Grocer", 20)]
        s = summarize(week, PLAN, PLAN_RULES, TODAY, SINCE)
        self.assertEqual((s.prior_weekly_avg, s.prior_days), (40, 7))
        s = summarize(days, PLAN, PLAN_RULES, TODAY, SINCE)
        self.assertEqual((s.prior_weekly_avg, s.prior_days), (0, 0))
        s = summarize([], PLAN, PLAN_RULES, TODAY, SINCE)
        self.assertEqual((s.prior_weekly_avg, s.prior_days), (0, 0))

    def test_covered_days_counts_both_ends_from_the_first_day(self):
        start, end = date(2026, 9, 1), date(2026, 9, 30)
        self.assertEqual(finance.covered_days(start, end, date(2026, 8, 1)), 30)
        self.assertEqual(finance.covered_days(start, end, start), 30)
        self.assertEqual(finance.covered_days(start, end, date(2026, 9, 21)), 10)
        self.assertEqual(finance.covered_days(start, end, end), 1)
        self.assertEqual(finance.covered_days(start, end, date(2026, 10, 2)), 0)
        self.assertEqual(finance.covered_days(start, end, None), 0)

    def test_last_months_one_offs_and_this_months_unmapped(self):
        txs = [spend(1, date(2026, 9, 1), "Sofa Shop", 700, one_off=True),
               spend(2, date(2026, 9, 30), "Bike Shop", 300, one_off=True),
               spend(3, date(2026, 8, 31), "TV Shop", 500, one_off=True),
               spend(4, date(2026, 10, 1), "Lamp Shop", 80, one_off=True),
               spend(5, date(2026, 9, 10), "Grocer", 60),
               spend(6, date(2026, 10, 2), "Mystery", 12, "Other", unmapped=True),
               spend(7, date(2026, 9, 2), "Mystery", 15, "Other", unmapped=True)]
        s = summarize(txs, PLAN, PLAN_RULES, TODAY, SINCE)
        self.assertEqual([t.id for t in s.one_offs_last_month], [1, 2])
        self.assertEqual([t.id for t in s.one_offs_mtd], [4])
        self.assertEqual([t.id for t in s.unmapped_mtd], [6])

    def test_daily_spending_by_day_this_month_and_last(self):
        txs = [spend(1, date(2026, 9, 1), "Grocer", 10), spend(2, date(2026, 9, 30), "Grocer", 20),
               spend(3, date(2026, 9, 30), "Bakery", 5), spend(4, date(2026, 8, 31), "Grocer", 99),
               spend(5, date(2026, 10, 1), "Grocer", 7), spend(6, TODAY, "Grocer", 3),
               spend(7, TODAY, "Sofa Shop", 900, one_off=True)]
        s = summarize(txs, PLAN, PLAN_RULES, TODAY, SINCE)
        self.assertEqual(s.daily_last_month, {1: 10, 30: 25})
        self.assertEqual(s.daily_this_month, {1: 7, 20: 3})

    def test_a_ledger_month_counts_the_first_and_today(self):
        rules = {**PLAN_RULES, "ledgers": [{"label": "Roof", "reimbursement_payees": ["Venmo"]}]}
        txs = [spend(1, date(2026, 10, 1), "Roofer", 100, kind=LEDGER, ledger="Roof"),
               spend(2, TODAY, "Roofer", 50, kind=LEDGER, ledger="Roof"),
               spend(3, TODAY, "Venmo", 0.5, kind=LEDGER, ledger="Roof"),     # a payment to them, not back
               spend(4, TODAY, "Venmo", -40, kind=LEDGER, ledger="Roof")]
        led = summarize(txs, PLAN, rules, TODAY, SINCE).ledgers[0]
        self.assertEqual((led.month, led.budget, led.costs, led.reimbursed), (110.5, 0.0, 150.5, 40))


class ThresholdsScaleWithTheCurrency(unittest.TestCase):
    """The thresholds are euro-sized; a yen household's are 150 times larger (fmt.scale). The plan
    and amounts are invented, in yen."""
    YEN_PLAN = Plan(fx_rate=1, lines=[Line("Food", "Groceries", None, 90_000, "Monthly", None),
                                      Line("Housing", "Rent", None, 150_000, "Monthly", None)])
    RULES = {"map": {}, "rules": {"day_to_day": ["Food"]}}

    def summary(self, scale: float, *txs: Tx):
        with mock.patch("butler.fmt.scale", return_value=scale):
            return summarize(list(txs), self.YEN_PLAN, self.RULES, TODAY, SINCE)

    def test_over_pace_needs_15000_yen_over(self):
        expected = 90_000 * 20 / 31                         # 58,065 by the 20th; a quarter is 14,516
        over = spend(1, date(2026, 10, 2), "Grocer", expected + 15_001)
        under = spend(1, date(2026, 10, 2), "Grocer", expected + 14_999)
        self.assertEqual([c for c, _, _ in self.summary(150, over).over_pace], ["Food"])
        self.assertEqual(self.summary(150, under).over_pace, [])
        self.assertEqual([c for c, _, _ in self.summary(1, under).over_pace], ["Food"])

    def test_no_budget_line_needs_7500_yen(self):
        gift = spend(1, date(2026, 10, 2), "Gift Shop", 7_400, "Gifts")
        self.assertEqual(self.summary(150, gift).unbudgeted, [])
        self.assertEqual(self.summary(1, gift).unbudgeted, [("Gifts", 7_400)])
        big = spend(1, date(2026, 10, 2), "Gift Shop", 7_501, "Gifts")
        self.assertEqual(self.summary(150, big).unbudgeted, [("Gifts", 7_501)])

    def test_a_bill_still_to_come_needs_2250_yen_left(self):
        rent = spend(1, date(2026, 10, 1), "Landlord", 148_000, "Housing")      # 2,000 under plan
        self.assertEqual(self.summary(150, rent).fixed_remaining, [])
        self.assertEqual(self.summary(1, rent).fixed_remaining, [("Rent", 2_000)])
        part = spend(1, date(2026, 10, 1), "Landlord", 147_700, "Housing")      # 2,300 to come
        self.assertEqual(self.summary(150, part).fixed_remaining, [("Rent", 2_300)])


class Build(unittest.TestCase):
    def test_build_fetches_the_window_caches_it_and_summarizes(self):
        fetched, cached = [], []
        raw_txs = [raw(id=7, date="2026-10-05", created_at="2026-10-05T09:00:00Z", payee="Grocer", to_base="42")]
        with mock.patch.object(finance, "load_rules", return_value=PLAN_RULES | {"map": {"Groceries": "Food"}}), \
             mock.patch.object(finance, "load_plan", return_value=PLAN), \
             mock.patch.object(finance.lunchmoney, "transactions",
                               side_effect=lambda start, end: fetched.append((start, end)) or raw_txs), \
             mock.patch.object(finance.lunchmoney, "cache",
                               side_effect=lambda name, data, day: cached.append((name, data, day))):
            s = finance.build(TODAY, SINCE)
        self.assertEqual(fetched, [(TODAY - timedelta(days=130), TODAY)])     # 16+ weeks for the drift watch
        self.assertEqual(cached, [("transactions", {"transactions": raw_txs}, TODAY)])
        self.assertEqual((s.mtd_by_category, s.tx_count, s.fx_rate_plan), ({"Food": 42}, 1, 0.86))


class Subscriptions(unittest.TestCase):
    """About-monthly charges: 2+ charges 25–35 days apart, the last two within 20%."""

    def charges(self, payee, days_ago, amounts, kind=SPEND, start_id=100, **kw):
        """Charges `days_ago`, oldest first; the earlier ones filed under "Unsorted", the latest under "Fun"."""
        if not isinstance(amounts, list):
            amounts = [amounts] * len(days_ago)
        return [spend(start_id + i, TODAY - timedelta(days=d), payee, a,
                      "Fun" if i == len(days_ago) - 1 else "Unsorted", kind=kind, **kw)
                for i, (d, a) in enumerate(zip(days_ago, amounts))]

    def test_which_payees_count_and_how_they_read(self):
        txs = (self.charges("Once", [50], 9, start_id=90)                          # checked first, skipped
               + self.charges("Streamy", [95, 64, 33], [99, 11, 12], start_id=1)     # gaps 31, 31
               + self.charges("Settled", [99, 80, 50, 20], [3, 2, 4, 4], start_id=50)  # gaps 19, 30, 30
               + self.charges("Price edge", [40, 10], [10, 12], start_id=60)       # up exactly 20%
               + self.charges("Gym", [50, 20], 30, kind=LEDGER, ledger="Side project", start_id=10)
               + self.charges("Short month", [65, 40], 8, start_id=20)              # gap 25
               + self.charges("Long month", [60, 25], 5, start_id=30)               # gap 35
               + self.charges("Tiny", [40, 10], 1, start_id=40))
        self.assertEqual(subscriptions(txs, TODAY), [
            {"payee": "Gym", "monthly": 30, "last": TODAY - timedelta(days=20), "kind": "Side project"},
            {"payee": "Streamy", "monthly": 12, "last": TODAY - timedelta(days=33), "kind": "Fun"},
            {"payee": "Price edge", "monthly": 12, "last": TODAY - timedelta(days=10), "kind": "Fun"},
            {"payee": "Short month", "monthly": 8, "last": TODAY - timedelta(days=40), "kind": "Fun"},
            {"payee": "Long month", "monthly": 5, "last": TODAY - timedelta(days=25), "kind": "Fun"},
            {"payee": "Settled", "monthly": 4, "last": TODAY - timedelta(days=20), "kind": "Fun"},
            {"payee": "Tiny", "monthly": 1, "last": TODAY - timedelta(days=10), "kind": "Fun"},
        ])

    def test_what_never_counts(self):
        cases = {
            "one charge": self.charges("Once", [10], 20),
            "weekly": self.charges("Weekly", [21, 14, 7], 20),
            "24 days apart": self.charges("Short", [44, 20], 20),
            "36 days apart": self.charges("Long", [56, 20], 20),
            "an old gap that was monthly": self.charges("Was monthly", [80, 50, 10], 20),
            "price jump over 20%": self.charges("Jump", [40, 10], [10, 12.5]),
            "refunds": self.charges("Refund", [40, 10], -20),
            "free": self.charges("Free", [40, 10], 0),
            "one-offs": self.charges("Sofa", [40, 10], 20, one_off=True),
            "transfers": self.charges("Bank", [40, 10], 20, kind=TRANSFER),
            "outside 100 days": self.charges("Old", [101, 71], 20),
            "the drawer's synthetic payments": [spend(-1, TODAY - timedelta(days=40), "Cash", 20),
                                                spend(-2, TODAY - timedelta(days=10), "Cash", 20)],
        }
        for name, txs in cases.items():
            with self.subTest(name):
                self.assertEqual(subscriptions(txs, TODAY), [])

    def test_charges_on_the_window_edges_and_in_any_order(self):
        txs = self.charges("Edge", [100, 70], 20) + self.charges("Today", [30, 0], 9, start_id=200)
        out = subscriptions(list(reversed(txs)), TODAY)
        self.assertEqual([(s["payee"], s["last"]) for s in out],
                         [("Edge", TODAY - timedelta(days=70)), ("Today", TODAY)])


if __name__ == "__main__":
    unittest.main()
