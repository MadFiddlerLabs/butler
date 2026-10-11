"""Property tests for the money code: invariants that must hold for any transactions, any figures
and any balances, not just the examples in test_finance.py. Hypothesis generates the inputs;
tests/props.py keeps it repeatable in CI. All payees, people and amounts are invented.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import calendar
import math
import re
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from hypothesis import assume, example, given
from hypothesis import strategies as st
from props import PROFILE

from butler import budget_changes, finance, fmt, networth
from butler.budget import Line, Plan
from butler.budget_changes import parse_amounts, within_bounds
from butler.finance import LEDGER, SPEND, classify, summarize

SYMBOLS = ("€", "EUR", "USD")
assert PROFILE   # tests/props.py has loaded the Hypothesis profile
KINDS = {finance.SPEND, finance.LEDGER, finance.INCOME, finance.RENTAL, finance.DRAW, finance.FUNDING,
         finance.TRANSFER, finance.IGNORED, finance.CASH_TOPUP, finance.MANAGED_FEE}
DAILY = ["Food", "Household"]

RULES = {
    "map": {"Groceries": "Food", "Restaurants": "Food", "Rent": "Housing", "Health": "Health",
            "Utilities": "Utilities", "Household": "Household", "Work": "EXCLUDE"},
    "overrides": [{"payee": "Corner Cafe", "category": "Food"},
                  {"payee": "Landlord", "category": "Housing", "label": "Rent", "next_month_from_day": 20},
                  {"payee": "Furniture Shop", "category": "Household", "one_off": True, "note": "new sofa"},
                  {"payee": "Swim School", "category": "Food", "fixed": True, "line": "Snacks"},
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
              "savings_draw_payee": "To Everyday", "day_to_day": DAILY, "fixed_lines": ["Snacks"]},
}

PLAN = Plan(fx_rate=0.86, lines=[
    Line("Food", "Groceries", "EUR", 900, "Monthly", None),
    Line("Food", "Snacks", "EUR", 60, "Monthly", None),
    Line("Household", "Household", "EUR", 150, "Monthly", None),
    Line("Housing", "Rent", "EUR", 1100, "Monthly", None),
    Line("Utilities", "Electricity", "EUR", 80, "Monthly", None),
    Line("Utilities", "Water", "USD", 40, "Monthly", None),
    Line("Childcare", "Babysitter", "EUR", 800, "Monthly", None),
    Line("Housing", "Property tax", "USD", 1000, "Annual", 10),
])

PAYEES = ["Grocer", "Corner Cafe", "Corner Cafe (Pending)", "Landlord", "Furniture Shop", "Swim School",
          "Hosting Co", "Roofing Co", "Venmo", "Tenant", "Bank", "To Cash Account", "To Everyday",
          "ADVISORY FEE", "Employer Expenses", "Shop", "Monthly Interest Paid", "Pharmacy"]
CATEGORIES = ["Groceries", "Restaurants", "Rent", "Health", "Utilities", "Household", "Work", "Side Project",
              "Mystery", ""]
ACCOUNTS = ["Everyday", "IRA", "Savings", "Rental", "Brokerage"]
TODAY = st.dates(min_value=date(2024, 1, 1), max_value=date(2030, 12, 31))
CENTS = st.integers(min_value=-500_000, max_value=500_000).map(lambda c: c / 100)


def fixed_symbols():
    p = mock.patch("butler.fmt._symbols", return_value=SYMBOLS)
    p.start()
    return p


@st.composite
def raw_tx(draw, today: date, i: int):
    day = today - timedelta(days=draw(st.integers(0, finance.FETCH_DAYS)))
    created = datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(
        hours=draw(st.integers(0, 96)))
    return {"id": i, "date": day.isoformat(), "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "payee": draw(st.sampled_from(PAYEES) | st.text(max_size=12)),
            "plaid_account_name": draw(st.sampled_from(ACCOUNTS)), "to_base": str(draw(CENTS)),
            "category_name": draw(st.sampled_from(CATEGORIES)),
            "status": draw(st.sampled_from(["cleared", "pending", "delete_pending"])),
            "exclude_from_totals": draw(st.booleans()), "is_income": draw(st.booleans())}


@st.composite
def month(draw):
    """A day, the transactions of the 130 days before it, and when the last brief went out."""
    today = draw(TODAY)
    raws = [draw(raw_tx(today, i)) for i in range(draw(st.integers(0, 40)))]
    since = datetime.combine(today - timedelta(days=1), datetime.min.time(), timezone.utc)
    return today, [classify(t, RULES) for t in raws], since


def close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


class FinanceProperties(unittest.TestCase):
    def setUp(self):
        self.addCleanup(fixed_symbols().stop)

    @given(TODAY.flatmap(lambda d: st.tuples(st.just(d), raw_tx(d, 1))))
    def test_classify_is_total(self, case):
        _, raw = case
        t = classify(raw, RULES)
        self.assertIn(t.kind, KINDS)
        if t.kind == SPEND:
            self.assertTrue(t.category)
            self.assertEqual(t.unmapped, t.category == "Other")
        if t.kind == LEDGER:
            self.assertIn(t.ledger, {"Side project", "Roof repair"})
        self.assertNotRegex(t.payee, r"\((Sent|Pending|Moved|Added|Refunded)\)$")

    @given(month())
    def test_the_three_kinds_of_spending_add_up_and_never_overlap(self, case):
        today, txs, since = case
        s = summarize(txs, PLAN, RULES, today, since)
        start = today.replace(day=1)
        mtd = [t for t in txs if t.kind == SPEND and start <= t.book_day <= today]
        self.assertTrue(close(s.mtd_total, sum(t.amount for t in mtd)))
        self.assertTrue(close(s.mtd_total, sum(s.mtd_by_category.values())))
        fixed = sum(v for _, v in s.bills_mtd)
        # Every transaction this month is exactly one of: one-off, fixed monthly, daily.
        self.assertTrue(close(s.mtd_total, s.one_offs_total + fixed + s.day_to_day_mtd),
                        (s.mtd_total, s.one_offs_total, fixed, s.day_to_day_mtd))
        self.assertTrue(close(s.one_offs_total, sum(t.amount for t in s.one_offs_mtd)))
        self.assertTrue(close(s.day_to_day_mtd, sum(s.daily_by_category.values())))
        self.assertLessEqual(set(s.daily_by_category), set(DAILY))

    @given(month(), st.integers(1, 200_000).map(lambda c: c / 100), st.integers(0, 40))
    def test_one_offs_never_enter_daily_pace(self, case, amount, back):
        today, txs, since = case
        day = today - timedelta(days=back)
        sofa = classify({"id": 99_999, "date": day.isoformat(), "created_at": f"{day}T10:00:00Z",
                         "payee": "Furniture Shop", "plaid_account_name": "Everyday", "to_base": str(amount),
                         "category_name": "Household", "status": "cleared"}, RULES)
        self.assertTrue(sofa.one_off)
        a = summarize(txs, PLAN, RULES, today, since)
        b = summarize(txs + [sofa], PLAN, RULES, today, since)
        for name in ("day_to_day_mtd", "last7", "prior_weekly_avg"):
            self.assertTrue(close(getattr(a, name), getattr(b, name)), name)
        self.assertEqual(a.daily_by_category.keys(), b.daily_by_category.keys())
        self.assertEqual([c for c, _, _ in a.over_pace], [c for c, _, _ in b.over_pace])
        self.assertEqual(a.daily_this_month, b.daily_this_month)
        in_month = day >= today.replace(day=1)
        self.assertTrue(close(b.one_offs_total, a.one_offs_total + (amount if in_month else 0)))
        self.assertTrue(close(b.mtd_total, a.mtd_total + (amount if in_month else 0)))
        self.assertEqual(sum(v for _, v in a.bills_mtd), sum(v for _, v in b.bills_mtd))

    @given(month())
    def test_only_household_spending_counts(self, case):
        """Ledgers, transfers, income, cash top-ups and fees never reach the household numbers."""
        today, txs, since = case
        everything = summarize(txs, PLAN, RULES, today, since)
        spend_only = summarize([t for t in txs if t.kind == SPEND], PLAN, RULES, today, since)
        for name in ("mtd_total", "day_to_day_mtd", "one_offs_total", "last7", "prior_weekly_avg"):
            self.assertTrue(close(getattr(everything, name), getattr(spend_only, name)), name)
        self.assertEqual(everything.mtd_by_category.keys(), spend_only.mtd_by_category.keys())

    @given(month(), st.randoms(use_true_random=False))
    def test_order_does_not_matter(self, case, rnd):
        today, txs, since = case
        shuffled = txs[:]
        rnd.shuffle(shuffled)
        a, b = summarize(txs, PLAN, RULES, today, since), summarize(shuffled, PLAN, RULES, today, since)
        self.assertTrue(close(a.mtd_total, b.mtd_total))
        self.assertTrue(close(a.day_to_day_mtd, b.day_to_day_mtd))
        self.assertEqual(sorted(c for c, _, _ in a.over_pace), sorted(c for c, _, _ in b.over_pace))
        self.assertEqual({l.label: round(l.month, 6) for l in a.ledgers}, {l.label: round(l.month, 6) for l in b.ledgers})

    @given(month())
    def test_pace_is_the_share_of_the_month_gone(self, case):
        today, txs, since = case
        s = summarize(txs, PLAN, RULES, today, since)
        days = calendar.monthrange(today.year, today.month)[1]
        self.assertEqual(s.days_in_month, days)
        self.assertEqual(s.pace_fraction, today.day / days)
        self.assertTrue(0 < s.pace_fraction <= 1)
        # Over pace means over by more than max(100, a quarter of what was expected by now), and
        # every daily category that is, is listed.
        flagged = {c for c, _, _ in s.over_pace}
        for cat, actual in s.daily_by_category.items():
            expected = s.daily_budget_by_category.get(cat, 0.0) * s.pace_fraction
            over = expected > 0 and actual - expected > max(100.0, 0.25 * expected)
            self.assertEqual(cat in flagged, over, cat)

    @given(month())
    def test_ledgers_add_up_on_their_own(self, case):
        today, txs, since = case
        s = summarize(txs, PLAN, RULES, today, since)
        start = today.replace(day=1)
        for led in s.ledgers:
            mine = [t for t in txs if t.kind == LEDGER and t.ledger == led.label]
            self.assertTrue(close(led.month, sum(t.amount for t in mine if start <= t.day <= today)))
            self.assertTrue(close(led.costs - led.reimbursed, sum(t.amount for t in mine)))
            self.assertGreaterEqual(led.reimbursed, 0)

    @given(st.dates(min_value=date(2020, 1, 1), max_value=date(2035, 12, 31)))
    @example(date(2026, 12, 20))
    @example(date(2028, 2, 29))
    def test_rent_paid_early_counts_toward_the_next_month(self, day):
        t = classify({"id": 1, "date": day.isoformat(), "created_at": f"{day}T08:00:00Z", "payee": "Landlord",
                      "plaid_account_name": "Everyday", "to_base": "1100", "category_name": "Rent"}, RULES)
        if day.day >= 20:
            nxt = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
            self.assertEqual(t.book_day, nxt)
        else:
            self.assertEqual(t.book_day, day)

    @given(TODAY, st.lists(st.integers(1, 50_000).map(lambda c: c / 100), max_size=10),
           st.integers(0, 1000))
    def test_the_cash_drawer_is_count_plus_top_ups_minus_payments(self, today, topups, counted):
        rules = {**RULES, "off_book": [], "cash_drawer": {
            "topup_payee": "ATM", "account": "Everyday",
            "recurring": {"label": "Nanny (cash)", "amount": 180, "weekday": "friday", "category": "Childcare",
                          "budget_line": "Babysitter", "since": str(today - timedelta(days=60))},
            "count": {"date": str(today - timedelta(days=30)), "amount": counted}}}
        raws = [{"id": i, "date": str(today - timedelta(days=i % 30)), "created_at": f"{today}T09:00:00Z",
                 "payee": "ATM", "plaid_account_name": "Everyday", "to_base": str(a), "category_name": ""}
                for i, a in enumerate(topups)]
        s = summarize([classify(t, rules) for t in raws], PLAN, rules, today,
                      datetime.combine(today, datetime.min.time(), timezone.utc))
        count_day = today - timedelta(days=30)
        paid = 180 * len([d for d in (count_day + timedelta(days=n) for n in range(1, 31)) if d.weekday() == 4])
        added = sum(a for i, a in enumerate(topups) if today - timedelta(days=i % 30) > count_day)
        self.assertTrue(close(s.drawer, counted + added - paid), (s.drawer, counted, added, paid))
        self.assertEqual(s.drawer_counted, count_day)


class AmountProperties(unittest.TestCase):
    """Figures from a reply: parsed in Python, never guessed."""

    @given(st.integers(0, 10**9))
    def test_whole_numbers_round_trip_in_every_grouping(self, n):
        for text in (str(n), f"{n:,}", f"{n:,}".replace(",", "."), f"{n:,}".replace(",", " "),
                     f"{n:,}".replace(",", " "), f"€{n:,}", f"{n:,}."):
            self.assertEqual(parse_amounts(text), [n], repr(text))

    @given(st.integers(0, 10**8), st.integers(0, 99))
    def test_decimals_round_trip_both_ways(self, whole, cents):
        value = whole + cents / 100
        english = f"{whole:,}.{cents:02d}"
        european = f"{whole:,}".replace(",", ".") + f",{cents:02d}"
        for text in (english, european, f"{whole}.{cents:02d}", f"{whole},{cents:02d}"):
            got = parse_amounts(text)
            self.assertEqual(len(got), 1, text)
            self.assertAlmostEqual(got[0], value, places=6, msg=text)

    @given(st.text())
    def test_any_text_is_safe(self, text):
        got = parse_amounts(text)
        self.assertEqual(len(got), len(budget_changes._TOKEN.findall(text)))
        for figure in got:
            self.assertTrue(figure is None or figure >= 0)

    @given(st.integers(1, 999), st.integers(1, 999))
    def test_a_group_that_is_not_three_digits_is_unclear(self, head, tail):
        assume(len(str(tail)) != 3)
        for sep in (",", ".", " "):
            got = parse_amounts(f"{head}{sep}{tail:0{len(str(tail))}d}{sep}000")
            self.assertEqual(got, [None], (head, sep, tail))

    @given(st.floats(-1e6, 1e6), st.floats(0, 1e6), st.floats(0, 1e6))
    def test_bounds(self, total, budget, trend):
        ok = within_bounds(total, budget, trend)
        self.assertEqual(ok, within_bounds(total, trend, budget), "the order of budget and trend doesn't matter")
        if ok:
            self.assertGreater(total, 0)
            self.assertGreaterEqual(total, 0.5 * min(budget, trend))
            self.assertLessEqual(total, 2.0 * max(budget, trend))


class PlanProperties(unittest.TestCase):
    """budget_changes.plan for any reply: either a question back, or a change that is in bounds,
    positive, and (when a figure was given) uses a figure the person really wrote."""

    LINES = [Line("Food", "Groceries", "EUR", 700, "Monthly", None),
             Line("Food", "Restaurants", "EUR", 250, "Monthly", None),
             Line("Transport", "Transit and taxis", "USD", 140, "Monthly", None)]
    OPEN = {"Food": {"budget": 950.0, "trend": 1100.0, "suggested": 1100},
            "Transport": {"budget": 120.4, "trend": 185.0, "suggested": 175}}

    def setUp(self):
        today = date(2026, 10, 2)
        self.today = today
        for p in (mock.patch.object(budget_changes, "pending", return_value=self.OPEN),
                  mock.patch.object(budget_changes, "daily_lines",
                                    side_effect=lambda c: [l for l in self.LINES if l.category == c]),
                  mock.patch.object(budget_changes, "load_plan", return_value=Plan(0.86, self.LINES))):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(fixed_symbols().stop)

    @given(st.sampled_from(["Food", "Transport", "Housing", ""]),
           st.none() | st.from_regex(r"[€$]?\d{1,5}([.,]\d{1,3})?( ?(EUR|USD))?", fullmatch=True) | st.text(max_size=8),
           st.text(max_size=40) | st.sampled_from(["Food: Groceries 800", "Transport 160", "Transport, yes",
                                                   "Restaurants 300 please", "Groceries"]))
    def test_a_change_is_always_safe(self, category, amount, said):
        said = f"{said} {amount}" if amount and len(said) % 2 else said
        result = budget_changes.plan({"category": category, "amount": amount}, said, self.today)
        if isinstance(result, str):
            return
        open_ = self.OPEN[result.category]
        fx = 0.86
        slack = fx / 2 if result.currency == "USD" else 0.005   # second-currency lines move in whole units
        self.assertGreater(result.new, 0)
        self.assertGreaterEqual(result.total_new, 0.5 * min(result.total_old, open_["trend"]) - slack)
        self.assertLessEqual(result.total_new, 2.0 * max(result.total_old, open_["trend"]) + slack)
        if amount and amount.strip():
            figure = parse_amounts(amount)[0]
            self.assertIn(figure, [f for f in parse_amounts(said) if f is not None])
            self.assertNotRegex(amount, r"\$|USD")


@st.composite
def accounts(draw, names):
    out = []
    for name in names:
        currency = draw(st.sampled_from(["eur", "usd"]))
        kind = draw(st.sampled_from(["depository", "investment", "credit"]))
        balance = draw(st.integers(-10**7, 10**7)) / 100
        out.append({"name": name, "type": kind, "currency": currency, "balance": balance})
    return out


def priced(accts, usd_rate):
    rows = [{**a, "value": a["balance"] * (usd_rate if a["currency"] == "usd" else 1.0)} for a in accts]
    return rows


@st.composite
def two_snapshots(draw):
    names = draw(st.lists(st.sampled_from([f"Account {c}" for c in "ABCDEFGH"]), unique=True, max_size=8))
    keep = [n for n in names if draw(st.booleans())]
    before = draw(accounts(names))
    now = [{**a, "balance": a["balance"] + draw(st.sampled_from([0, draw(st.integers(-10**6, 10**6)) / 100]))}
           for a in before if a["name"] in keep]
    now += draw(accounts([f"New {i}" for i in range(draw(st.integers(0, 2)))]))
    then_rate = draw(st.integers(7000, 10000)) / 10000
    now_rate = draw(st.sampled_from([then_rate, draw(st.integers(7000, 10000)) / 10000]))
    b, n = priced(before, then_rate), priced(now, now_rate)
    return ({"date": "2026-10-09", "total": sum(a["value"] for a in n), "accounts": n},
            {"date": "2026-10-08", "total": sum(a["value"] for a in b), "accounts": b})


class NetWorthProperties(unittest.TestCase):
    """Exchange-rate moves are split from real ones, and together they explain the whole change."""

    def setUp(self):
        self.addCleanup(fixed_symbols().stop)

    @given(two_snapshots())
    def test_fx_plus_real_moves_explain_the_change(self, snaps):
        now, before = snaps
        ch = networth.compare(now, before)
        unexplained = (ch.total - ch.previous) - ch.fx - sum(m.real for m in ch.moves)
        # Moves under 1 are left out of the explanation; nothing else may be.
        accounts = {a["name"] for a in now["accounts"]} | {a["name"] for a in before["accounts"]}
        self.assertLessEqual(abs(unexplained), len(accounts) * 1.0 + 1e-6, (ch, unexplained))
        self.assertEqual([abs(m.real) for m in ch.moves], sorted((abs(m.real) for m in ch.moves), reverse=True))

    @given(two_snapshots())
    def test_euro_accounts_never_have_an_exchange_rate_effect(self, snaps):
        now, before = snaps
        for a in now["accounts"] + before["accounts"]:
            if a["currency"] == "usd":
                a["currency"] = "eur"
                a["value"] = a["balance"]
        now["total"] = sum(a["value"] for a in now["accounts"])
        before["total"] = sum(a["value"] for a in before["accounts"])
        self.assertTrue(close(networth.compare(now, before).fx, 0.0))

    @given(two_snapshots())
    def test_same_balances_mean_no_real_moves(self, snaps):
        _, before = snaps
        rate = 0.81
        now = {"date": "2026-10-09", "accounts": [
            {**a, "value": a["balance"] * (rate if a["currency"] == "usd" else 1.0)} for a in before["accounts"]]}
        now["total"] = sum(a["value"] for a in now["accounts"])
        ch = networth.compare(now, before)
        self.assertEqual(ch.moves, [])
        self.assertTrue(close(ch.total - ch.previous, ch.fx))

    @given(two_snapshots(), st.integers(1, 10_000))
    def test_the_line_says_up_or_down_and_explains_only_notable_changes(self, snaps, notable):
        now, before = snaps
        assume(any(a["currency"] == "usd" and a["balance"] > 0 for a in now["accounts"]))
        text = networth.line(now, networth.compare(now, before), "since yesterday", notable)
        delta = now["total"] - before["total"]
        self.assertIn(" up " if delta >= 0 else " down ", text)
        if abs(delta) < notable:
            self.assertNotIn("What changed", text)
        self.assertNotRegex(text, r"[$€]-", "a minus sign goes before the currency symbol")


class FormatProperties(unittest.TestCase):
    def setUp(self):
        self.addCleanup(fixed_symbols().stop)

    @staticmethod
    def read(text: str, symbol: str) -> int:
        negative = text.startswith("−")
        digits = text.removeprefix("−").removeprefix(symbol).replace(",", "")
        assert re.fullmatch(r"\d+", digits), text
        return -int(digits) if negative else int(digits)

    @given(st.floats(-1e12, 1e12, allow_nan=False))
    @example(-0.5)
    @example(-0.51)
    @example(2.5)
    def test_money_shows_the_rounded_amount_with_the_sign_first(self, x):
        text = fmt.money(x)
        self.assertNotIn("-", text, "the minus is U+2212, before the symbol")
        self.assertEqual(self.read(text, "€"), round(x))
        self.assertNotEqual(text, "−€0")

    @given(st.floats(-1e12, 1e12, allow_nan=False))
    @example(-1234.0)
    def test_second_currency_money_reads_the_same_way(self, x):
        text = fmt.second_money(x)
        self.assertNotIn("-", text)
        self.assertEqual(self.read(text, "$"), round(x))
        self.assertNotEqual(text, "−$0")

    @given(st.text(alphabet=st.characters(categories=["Lu"]), min_size=1, max_size=4))
    def test_currency_names_never_fail(self, code):
        self.assertTrue(fmt.name(code))


if __name__ == "__main__":
    unittest.main()
