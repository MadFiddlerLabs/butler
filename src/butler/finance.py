"""Turns Lunch Money transactions into the numbers the brief reports. Pure arithmetic; no
model calls. Rules live in config/category_map.yaml (see category_map.example.yaml).

Lunch Money conventions: positive amounts are money out, negative are money in, and `to_base`
is the amount in your primary currency at Lunch Money's daily rate. In this module `amount`
always means the primary-currency amount.

Spending is split three ways, and never mixed:
- fixed monthly expenses: budget lines in non-daily categories (rent, therapy, storage), plus
  anything marked `fixed`; reported as paid / still to come against the fixed plan;
- daily spending: the `day_to_day` categories; paced through the month and trended;
- one-offs: marked `one_off`; counted in the month's total, named, and kept out of both.
"""

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import yaml

from . import fmt, lunchmoney
from .budget import Line, Plan, load_plan
from .config import config_path

FETCH_DAYS = 130   # 16+ weeks, for the weekly letter's drift watch

SPEND, LEDGER, INCOME, RENTAL, DRAW, FUNDING, TRANSFER, IGNORED, CASH_TOPUP, MANAGED_FEE = (
    "spend", "ledger", "income", "rental_income", "savings_draw", "cash_funding", "transfer",
    "ignored", "cash_topup", "managed_fee")


@dataclass
class Tx:
    id: int
    day: date
    created: datetime
    payee: str
    account: str
    amount: float
    kind: str
    category: str = ""
    one_off: bool = False
    note: str = ""
    unmapped: bool = False
    recurring_id: int | None = None
    fixed: bool = False            # a fixed monthly cost even if its category is paced daily
    line: str = ""                 # for fixed costs in daily categories: the budget line paid
    ledger: str = ""               # for kind LEDGER: which ledger
    book_day: date | None = None   # the day it counts toward in budgets; usually `day`

    def __post_init__(self):
        if self.book_day is None:
            self.book_day = self.day


def load_rules() -> dict:
    with open(config_path("category_map")) as f:
        return yaml.safe_load(f)


def _clean_payee(name: str) -> str:
    # Some banks (Wise, for one) append "(Sent)", "(Pending)", "(Moved)" and the like.
    return re.sub(r"\s*\((?:Sent|Pending|Moved|Added|Refunded)\)\s*$", "", name or "").strip()


def _match(rule: dict, payee: str, account: str) -> bool:
    if rule["payee"].lower() not in payee.lower():
        return False
    return "account" not in rule or rule["account"] == account


def _has(names, text: str) -> bool:
    return any(n.lower() in text.lower() for n in names or [])


def classify(t: dict, rules: dict) -> Tx:
    r = rules["rules"]
    account = t.get("plaid_account_name") or t.get("asset_name") or ""
    institution = t.get("institution_name") or ""
    payee = _clean_payee(t.get("payee", ""))
    amount = float(t.get("to_base") or 0)
    tx = Tx(id=t["id"], day=date.fromisoformat(t["date"]), recurring_id=t.get("recurring_id"),
            created=datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")),
            payee=payee, account=account, amount=amount, kind=IGNORED)

    for o in rules.get("overrides") or []:
        if _match(o, payee, account):
            if o["category"] == "EXCLUDE":      # not household spending
                return tx
            tx.kind, tx.category, tx.one_off = SPEND, o["category"], o.get("one_off", False)
            tx.note, tx.payee = o.get("note", ""), o.get("label", payee)
            tx.fixed, tx.line = o.get("fixed", False), o.get("line", "")
            cutoff = o.get("next_month_from_day")
            if cutoff and tx.day.day >= cutoff:
                tx.book_day = (tx.day.replace(day=28) + timedelta(days=4)).replace(day=1)
            return tx

    fees = rules.get("managed_fees")
    if fees and fees["payee"].lower() in payee.lower():
        tx.kind = MANAGED_FEE
        return tx
    ignored_institution = _has(r.get("ignore_institutions"), institution)
    for led in rules.get("ledgers") or []:
        if _has(led.get("payees"), payee) and not ignored_institution:
            tx.kind, tx.ledger = LEDGER, led["label"]
            return tx
    drawer = rules.get("cash_drawer")
    if drawer and drawer["topup_payee"].lower() in payee.lower() and drawer["account"] == account:
        tx.kind = CASH_TOPUP
        return tx
    if _has(r.get("ignore_accounts"), account) or ignored_institution \
            or t.get("status") in r.get("ignore_statuses", []):
        return tx
    for led in rules.get("ledgers") or []:
        since = led.get("reimbursements_from")
        if amount < 0 and _has(led.get("reimbursement_payees"), payee) and (
                not since or tx.day >= date.fromisoformat(str(since))):
            tx.kind, tx.ledger = LEDGER, led["label"]
            return tx
    if any(_match(c, payee, account) for c in rules.get("cash_funding") or []):
        tx.kind = FUNDING
        return tx
    if account == r.get("savings_account") and amount > 0 and \
            r.get("savings_draw_payee", "").lower() in payee.lower():
        tx.kind = DRAW
        return tx
    if "interest" in payee.lower() and amount < 0:
        tx.kind = INCOME      # bank interest, even on a rental account
        return tx
    if account == r.get("rental_income_account") and amount < 0 and not t.get("exclude_from_totals"):
        tx.kind = RENTAL
        return tx
    if t.get("exclude_from_totals"):
        tx.kind = TRANSFER
        return tx
    if t.get("is_income"):
        tx.kind = INCOME
        return tx

    category = t.get("category_name") or ""
    for led in rules.get("ledgers") or []:
        if category in (led.get("categories") or []):
            tx.kind, tx.ledger = LEDGER, led["label"]
            return tx
    mapped = rules["map"].get(category)
    if mapped == "EXCLUDE":                     # not household spending
        return tx
    tx.kind, tx.category, tx.unmapped = SPEND, mapped or "Other", mapped is None
    return tx


@dataclass
class LedgerSummary:
    label: str
    month: float          # this month, net
    budget: float         # monthly budget, 0 if none
    costs: float          # everything in the fetch window, not counting reimbursements
    reimbursed: float     # money back (from `reimbursement_payees`)


@dataclass
class Summary:
    today: date
    since: datetime
    new: list[Tx]
    mtd_total: float
    mtd_by_category: dict[str, float]           # everything this month, one-offs included
    against_budget_by_category: dict[str, float]   # the same without one-offs: what budgets compare with
    budget_month: float
    budget_by_category: dict[str, float]
    day_to_day_mtd: float
    day_to_day_budget: float
    bills_mtd: list[tuple[str, float]]
    fixed_plan: list[tuple[str, float]]
    fixed_remaining: list[tuple[str, float]]
    fixed_paid: list[tuple[str, float]]
    one_offs_total: float
    daily_budget_by_category: dict[str, float]
    daily_by_category: dict[str, float]
    lumps_due: list[tuple[str, float]]
    pace_fraction: float
    over_pace: list[tuple[str, float, float]]
    unbudgeted: list[tuple[str, float]]
    last7: float
    prior_weekly_avg: float     # a week's daily spending, over the days before the last seven
    prior_days: int             # those days: 28, fewer for a new household, 0 under a week
    draw_mtd: float
    funding_mtd: float
    rental_mtd: float
    one_offs_mtd: list[Tx]
    one_offs_last_month: list[Tx]
    unmapped_mtd: list[Tx]
    off_book: list[tuple[str, float, str]]
    ledgers: list[LedgerSummary]
    cash_topups_mtd: float
    cash_topups_8w: float
    cash_payments_8w: float
    cash_payment_label: str
    drawer: float | None
    drawer_counted: date | None
    managed_fees_mtd: float
    managed_fees_label: str
    managed_fee_rate: float
    managed_balance: float
    savings_balance: float | None
    draw_monthly: float         # 0 until there's a full month of draws
    tx_count: int
    fx_rate_plan: float
    txs: list[Tx]
    daily_this_month: dict[int, float]
    daily_last_month: dict[int, float]
    days_in_month: int
    new_by_category: dict[str, float]           # new since the last brief, one-offs and fixed lines left out
    problems: list[str] = field(default_factory=list)


def _group_names(plan: Plan, off_book: set[str], daily: set[str], fixed_items: set[str]) -> dict[str, str]:
    """Maps a fixed category or fixed line to its name in the fixed plan: a category with one
    line is named after that line ("Rent"), otherwise after the category ("Utilities")."""
    groups: dict[str, list] = {}
    for l in plan.lines:
        if l.frequency == "Monthly" and l.item not in off_book and (
                l.category not in daily or l.item in fixed_items):
            groups.setdefault(l.item if l.item in fixed_items else l.category, []).append(l)
    return {key: (ls[0].item if len(ls) == 1 else key) for key, ls in groups.items()}


def _fixed_plan(plan: Plan, off_book: set[str], daily: set[str], fixed_items: set[str]) -> list[tuple[str, float]]:
    groups: dict[str, list] = {}
    for l in plan.lines:
        if l.frequency == "Monthly" and l.item not in off_book and (
                l.category not in daily or l.item in fixed_items):
            groups.setdefault(l.item if l.item in fixed_items else l.category, []).append(l)
    out = [((ls[0].item if len(ls) == 1 else cat), sum(l.amount_primary(plan.fx_rate) for l in ls))
           for cat, ls in groups.items()]
    return sorted((x for x in out if x[1] > 0), key=lambda x: -x[1])


def _fixed_status(plan: Plan, mtd: list[Tx], off_book: set[str], daily: set[str], fixed_items: set[str],
                  unit: float) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """(still to come, paid) per fixed-plan entry. `unit` scales the thresholds (fmt.scale)."""
    names = _group_names(plan, off_book, daily, fixed_items)
    paid: dict[str, float] = {}
    for t in mtd:
        key = t.line if t.line in names else (t.category if t.category in names else None)
        if key and (t.category not in daily or t.fixed):
            paid[key] = paid.get(key, 0.0) + t.amount
    planned = dict(_fixed_plan(plan, off_book, daily, fixed_items))
    left = [(name, planned.get(name, 0.0) - paid.get(key, 0.0)) for key, name in names.items()]
    # Gaps under 15 are bills coming in slightly under plan, not bills still to come.
    still = sorted(((n, v) for n, v in left if v >= 15 * unit), key=lambda x: -x[1])
    done = sorted(((names[k], v) for k, v in paid.items() if v >= 1), key=lambda x: -x[1])
    return still, done


def subscriptions(txs: list[Tx], today: date) -> list[dict]:
    """Payees charging about monthly (2+ charges 25–35 days apart, amounts within 20%) in the
    last ~100 days, household and ledgers alike."""
    by: dict[str, list[Tx]] = {}
    for t in txs:
        if t.kind in (SPEND, LEDGER) and t.amount > 0 and not t.one_off and t.id >= 0 \
                and today - timedelta(days=100) <= t.day <= today:
            by.setdefault(t.payee, []).append(t)
    out = []
    for payee, ts in by.items():
        ts.sort(key=lambda t: t.day)
        if len(ts) < 2:
            continue
        gaps = [(b.day - a.day).days for a, b in zip(ts, ts[1:])]
        amounts = [t.amount for t in ts]
        if all(25 <= g <= 35 for g in gaps[-2:]) and max(amounts[-2:]) <= 1.2 * min(amounts[-2:]):
            out.append({"payee": payee, "monthly": amounts[-1], "last": ts[-1].day,
                        "kind": ts[-1].ledger or ts[-1].category})
    return sorted(out, key=lambda x: -x["monthly"])


def _snapshot_sum(predicate) -> float | None:
    from . import networth
    snaps = networth.load()
    if not snaps:
        return None
    return sum(a["value"] for a in snaps[-1]["accounts"] if predicate(a["name"]))


def _weekdays(start: date, end: date, weekday: int) -> list[date]:
    d = start + timedelta(days=(weekday - start.weekday()) % 7)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}


def _recurring_cash(rules: dict, start: date, end: date) -> list[Tx]:
    """A recurring cash payment from the drawer (e.g. a weekly nanny or cleaner) as
    transactions on each payday in [start, end]."""
    rec = (rules.get("cash_drawer") or {}).get("recurring")
    if not rec:
        return []
    since = date.fromisoformat(str(rec.get("since", start)))
    weekday = WEEKDAYS[rec.get("weekday", "friday").lower()]
    return [Tx(id=-int(d.strftime("%Y%m%d")), day=d,
               created=datetime.combine(d, datetime.min.time()).replace(tzinfo=timezone.utc),
               payee=rec["label"], account="cash drawer", amount=float(rec["amount"]),
               kind=SPEND, category=rec["category"], fixed=True, line=rec.get("budget_line", ""))
            for d in _weekdays(max(start, since), end, weekday)]


def _drawer(txs: list[Tx], rules: dict, today: date) -> tuple[float | None, date | None]:
    """Last drawer count + top-ups since − recurring cash payments since. Other cash spending
    isn't known, so the real drawer holds at most this much."""
    drawer = rules.get("cash_drawer") or {}
    count = drawer.get("count")
    if not count:
        return None, None
    counted = date.fromisoformat(str(count["date"]))
    est = float(count["amount"])
    label = (drawer.get("recurring") or {}).get("label")
    for t in txs:
        if counted < t.day <= today:
            if t.kind == CASH_TOPUP:
                est += t.amount
            elif label and t.payee == label:
                est -= t.amount
    return est, counted


def covered_days(start: date, end: date, first: date | None) -> int:
    """How many days of [start, end] the data covers when its earliest day is `first`. Averages
    divide by this, not the whole window, so missing history doesn't count as days of zero."""
    return 0 if first is None else max(0, (end - max(start, first)).days + 1)


def summarize(txs: list[Tx], plan: Plan, rules: dict, today: date, since: datetime) -> Summary:
    r = rules["rules"]
    unit = fmt.scale()      # thresholds below are euro-sized; ¥ and kr households scale them
    month_start = today.replace(day=1)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    month_end = month_start + timedelta(days=days_in_month - 1)
    frac = today.day / days_in_month

    # A recurring cash payment: the plan is the amount × paydays this month (from its start),
    # and what's been paid is the amount × paydays up to today.
    rec = (rules.get("cash_drawer") or {}).get("recurring")
    if rec:
        weekday = WEEKDAYS[rec.get("weekday", "friday").lower()]
        start = max(month_start, date.fromisoformat(str(rec.get("since", month_start))))
        planned = rec["amount"] * len(_weekdays(start, month_end, weekday))
        plan = Plan(plan.fx_rate, [Line(l.category, l.item, None, planned, l.frequency, l.due_month, l.note)
                                   if l.item == rec.get("budget_line") else l for l in plan.lines])
        txs = txs + _recurring_cash(rules, today - timedelta(days=FETCH_DAYS), today)

    off_book_items = {o["item"] for o in rules.get("off_book") or []} | set(r.get("netted_from_rent", []))
    fixed_items = set(r.get("fixed_lines", []))
    daily_cats = set(r["day_to_day"])
    budget = plan.monthly_by_category(exclude_items=off_book_items)
    daily_budget = plan.monthly_by_category(exclude_items=off_book_items | fixed_items)

    spend = [t for t in txs if t.kind == SPEND]
    mtd = [t for t in spend if month_start <= t.book_day <= today]
    by_cat: dict[str, float] = {}
    planned_cat: dict[str, float] = {}
    daily_by_cat: dict[str, float] = {}
    for t in mtd:
        by_cat[t.category] = by_cat.get(t.category, 0.0) + t.amount
        if not t.one_off:
            planned_cat[t.category] = planned_cat.get(t.category, 0.0) + t.amount
        if t.category in daily_cats and not t.fixed and not t.one_off:
            daily_by_cat[t.category] = daily_by_cat.get(t.category, 0.0) + t.amount

    over = []
    for cat, actual in daily_by_cat.items():
        expected = daily_budget.get(cat, 0.0) * frac
        if expected > 0 and actual - expected > max(100.0 * unit, 0.25 * expected):
            over.append((cat, actual, expected))
    over.sort(key=lambda o: o[2] - o[1])
    unbudgeted = sorted(((c, a) for c, a in planned_cat.items() if budget.get(c, 0.0) == 0 and a > 50 * unit),
                        key=lambda x: -x[1])

    recurring = [t for t in spend if not t.one_off and not t.fixed and t.category in daily_cats]
    week_start = today - timedelta(days=6)
    last7 = sum(t.amount for t in recurring if week_start <= t.day <= today)
    prior = sum(t.amount for t in recurring if week_start - timedelta(days=28) <= t.day < week_start)
    prior_days = covered_days(week_start - timedelta(days=28), week_start - timedelta(days=1),
                              min((t.day for t in recurring), default=None))
    prior_days = prior_days if prior_days >= 7 else 0      # under a week: nothing to compare with
    # The savings draw per month, from the first draw over at most the last 90 days. Runway needs
    # a full month of draws, so a first draw last week doesn't promise years of savings.
    draws = [t for t in txs if t.kind == DRAW]
    draw_days = covered_days(today - timedelta(days=89), today, min((t.day for t in draws), default=None))
    draw_total = sum(t.amount for t in draws if today - timedelta(days=89) <= t.day <= today)

    def mtd_sum(kind: str) -> float:
        return sum(t.amount for t in txs if t.kind == kind and month_start <= t.day <= today)

    def daily(start: date, end: date) -> dict[int, float]:
        out: dict[int, float] = {}
        for t in spend:
            if start <= t.book_day <= end and not t.one_off:
                out[t.book_day.day] = out.get(t.book_day.day, 0.0) + t.amount
        return out

    last_month_end = month_start - timedelta(days=1)
    # New = arrived since the last brief and dated recently; a newly connected account's
    # imported history isn't news.
    recent = since.date() - timedelta(days=7)
    new = sorted([t for t in spend if t.created > since and t.day >= recent], key=lambda t: -t.amount)
    new_by_cat: dict[str, float] = {}
    for t in new:
        if not t.one_off and not t.fixed:     # the chart's "+€x new" is daily spending only
            new_by_cat[t.category] = new_by_cat.get(t.category, 0.0) + t.amount

    ledgers = []
    for led in rules.get("ledgers") or []:
        mine = [t for t in txs if t.kind == LEDGER and t.ledger == led["label"]]
        is_back = lambda t: _has(led.get("reimbursement_payees"), t.payee) and t.amount < 0  # noqa: E731
        ledgers.append(LedgerSummary(
            label=led["label"], budget=float(led.get("monthly_budget", 0)),
            month=sum(t.amount for t in mine if month_start <= t.day <= today),
            costs=sum(t.amount for t in mine if not is_back(t)),
            reimbursed=-sum(t.amount for t in mine if is_back(t))))

    real_off_book = {o["item"]: o.get("paid", "") for o in rules.get("off_book") or []}
    not_one_off = [t for t in mtd if not t.one_off]
    still, done = _fixed_status(plan, not_one_off, off_book_items, daily_cats, fixed_items, unit)
    drawer, counted = _drawer(txs, rules, today)
    fees = rules.get("managed_fees") or {}
    label = (rec or {}).get("label", "")
    return Summary(
        today=today, since=since, new=new,
        mtd_total=sum(by_cat.values()), mtd_by_category=by_cat, against_budget_by_category=planned_cat,
        budget_month=sum(budget.values()), budget_by_category=budget,
        day_to_day_mtd=sum(daily_by_cat.values()),
        day_to_day_budget=sum(b for c, b in daily_budget.items() if c in daily_cats),
        bills_mtd=sorted(_by_payee(t for t in not_one_off if t.category not in daily_cats or t.fixed).items(),
                         key=lambda x: -x[1]),
        fixed_plan=_fixed_plan(plan, off_book_items, daily_cats, fixed_items),
        fixed_remaining=still, fixed_paid=done,
        one_offs_total=sum(t.amount for t in mtd if t.one_off),
        daily_budget_by_category={c: b for c, b in daily_budget.items() if c in daily_cats},
        daily_by_category=daily_by_cat,
        lumps_due=[(l.item, l.amount_primary(plan.fx_rate)) for l in plan.lumps_due(today.month)],
        pace_fraction=frac, over_pace=over, unbudgeted=unbudgeted,
        last7=last7, prior_weekly_avg=prior * 7 / prior_days if prior_days else 0.0, prior_days=prior_days,
        draw_mtd=mtd_sum(DRAW), funding_mtd=mtd_sum(FUNDING), rental_mtd=-mtd_sum(RENTAL),
        one_offs_mtd=[t for t in mtd if t.one_off],
        one_offs_last_month=[t for t in spend if t.one_off
                             and last_month_end.replace(day=1) <= t.book_day <= last_month_end],
        unmapped_mtd=[t for t in mtd if t.unmapped],
        off_book=[(l.item, l.amount_primary(plan.fx_rate), real_off_book[l.item])
                  for l in plan.lines if l.item in real_off_book],
        ledgers=ledgers,
        cash_topups_mtd=sum(t.amount for t in txs if t.kind == CASH_TOPUP and month_start <= t.day <= today),
        cash_topups_8w=sum(t.amount for t in txs if t.kind == CASH_TOPUP and today - timedelta(days=55) <= t.day <= today),
        cash_payments_8w=sum(t.amount for t in txs if label and t.payee == label
                             and today - timedelta(days=55) <= t.day <= today),
        cash_payment_label=label, drawer=drawer, drawer_counted=counted,
        managed_fees_mtd=mtd_sum(MANAGED_FEE), managed_fees_label=fees.get("label", "Advisor fees"),
        managed_fee_rate=float(fees.get("annual_rate", 0)),
        managed_balance=(_snapshot_sum(lambda n: n.startswith(fees["managed_institution"])) or 0.0)
        if fees.get("managed_institution") else 0.0,
        savings_balance=_snapshot_sum(lambda n: n.endswith(r.get("savings_account") or "\0")),
        draw_monthly=draw_total * 30 / draw_days if draw_days >= 30 else 0.0,
        tx_count=len(txs), fx_rate_plan=plan.fx_rate, txs=txs,
        daily_this_month=daily(month_start, today),
        daily_last_month=daily(last_month_end.replace(day=1), last_month_end),
        days_in_month=days_in_month, new_by_category=new_by_cat,
    )


def _by_payee(txs) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in txs:
        out[t.payee] = out.get(t.payee, 0.0) + t.amount
    return out


def build(today: date, since: datetime) -> Summary:
    rules = load_rules()
    raw = lunchmoney.transactions(today - timedelta(days=FETCH_DAYS), today)
    lunchmoney.cache("transactions", {"transactions": raw}, today)
    txs = [classify(t, rules) for t in raw]
    return summarize(txs, load_plan(), rules, today, since)
