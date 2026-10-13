"""The weekly letter: the week that was, the week ahead, money, drift, ideas, projects. It goes
out on email.weekly_day (Sunday unless set) and covers the seven days ending that day; its
wording names that day.

    python -m butler.weekly --dry-run   # writes out/weekly-YYYY-WW.html/.txt
    python -m butler.weekly             # also sends it and saves reports/YYYY-WW.md

Python does every sum; Claude writes the headline, picks the week's focus, suggests
save/earn ideas and words the project notes, from the numbers it's handed.
"""

import argparse
import copy
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import charts, finance, llm, mailer, networth, projects
from .agenda import Agenda, load_agenda
from .config import ROOT, brand, load_settings, people, portal_url, tz
from .finance import SPEND, Summary, Tx
from .fmt import money, primary_code

log = logging.getLogger("butler")
IDEAS = ROOT / "data" / "ideas.jsonl"
QUESTIONS = ROOT / "data" / "questions.json"   # id -> {payee, text, asked}; replies resolve ids
REPORTS = ROOT / "reports"
DRIFT_MIN = 15   # in the primary currency


# ---------- the day ----------

def letter_weekday(settings: dict) -> int:
    """The day the letter goes out: Monday = 0 ... Sunday = 6 (the default)."""
    return int(settings.get("email", {}).get("weekly_day", 6))


def letter_day_name(settings: dict) -> str:
    """'Sunday', or whichever day email.weekly_day names."""
    return ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")[letter_weekday(settings)]


def next_letter_day(settings: dict, today: date) -> date:
    """The next day the letter goes out, today included."""
    return today + timedelta(days=(letter_weekday(settings) - today.weekday()) % 7)


def _part_of_day(hour: int) -> str:
    return "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"


# ---------- numbers ----------

@dataclass
class CategoryRow:
    category: str
    budget: float
    spent: float
    expected: float     # by today: pro rata for daily spending, the full bill otherwise
    flag: str           # "over", "under" or ""


@dataclass
class Drift:
    payee: str
    last4: float        # last 4 weeks
    before: float       # 4-week equivalent of the 12 weeks before
    note: str = ""


@dataclass
class Week:
    last_day: date                  # the letter's day, the last of the week it covers
    week_spend: float               # daily spending, the seven days just ending
    typical_week: float             # daily spending, weekly average of the 8 weeks before
    rows: list[CategoryRow]
    on_track: list[str]
    drift: list[Drift]
    questions: list[tuple[str, str, str]]
    lumps_60d: list[tuple[str, date, float, str]]   # item, month due, amount, category
    annual_set_aside: float
    money: Summary
    ahead: Agenda | None
    routines: list[str] = field(default_factory=list)
    ledger_week: dict[str, list[tuple[str, float]]] = field(default_factory=dict)  # ledger -> by payee
    networth: str = ""


def _week_numbers(m: Summary, rules: dict, last_day: date) -> tuple[float, float]:
    daily = set(rules["rules"]["day_to_day"])
    spend = [t for t in m.txs if t.kind == SPEND and not t.one_off and t.category in daily]
    first_day = last_day - timedelta(days=6)
    week = sum(t.amount for t in spend if first_day <= t.day <= last_day)
    prior = sum(t.amount for t in spend if first_day - timedelta(days=56) <= t.day < first_day)
    return week, prior / 8


def _category_rows(m: Summary, rules: dict) -> tuple[list[CategoryRow], list[str]]:
    daily = set(rules["rules"]["day_to_day"])
    rows, on_track = [], []
    for cat in sorted(set(m.budget_by_category) | set(m.mtd_by_category)):
        budget, spent = m.budget_by_category.get(cat, 0.0), m.mtd_by_category.get(cat, 0.0)
        expected = budget * m.pace_fraction if cat in daily else budget
        gap = spent - expected
        threshold = max(100.0, 0.10 * budget)
        if gap > threshold:
            rows.append(CategoryRow(cat, budget, spent, expected, "over"))
        elif budget:
            on_track.append(cat)
    return rows, on_track


def _drift(m: Summary, rules: dict, last_day: date) -> list[Drift]:
    ignore = {p.lower() for p in rules.get("drift_ignore", [])}
    spend = [t for t in m.txs if t.kind == SPEND and not t.one_off
             and not any(i in t.payee.lower() for i in ignore)]
    start4 = last_day - timedelta(days=27)
    start16 = start4 - timedelta(days=84)
    by_payee: dict[str, list[float]] = {}
    for t in spend:
        if start16 <= t.day <= last_day:
            slot = by_payee.setdefault(t.payee, [0.0, 0.0, 0])
            recent = t.day >= start4
            slot[0 if recent else 1] += t.amount
            slot[2] += 1 if recent else 0
    out = []
    for payee, (last4, prior12, recent_count) in by_payee.items():
        before = prior12 / 3
        if prior12 == 0 and recent_count < 2:
            continue  # a single new purchase isn't drift
        if last4 - before >= max(DRIFT_MIN, 0.3 * before) and last4 >= DRIFT_MIN * 2:
            out.append(Drift(payee, last4, before, "new" if prior12 == 0 else ""))
    out.sort(key=lambda d: d.before - d.last4)

    # Recurring charges whose amount changed (Lunch Money recurring links).
    series: dict[int, list[Tx]] = {}
    for t in spend:
        if t.recurring_id:
            series.setdefault(t.recurring_id, []).append(t)
    for txs in series.values():
        txs.sort(key=lambda t: t.day)
        if len(txs) >= 2 and abs(txs[-1].amount - txs[-2].amount) >= max(2.0, 0.05 * txs[-2].amount):
            out.append(Drift(txs[-1].payee, txs[-1].amount, txs[-2].amount, "recurring charge changed"))
    return out[:6]


def _questions(m: Summary, rules: dict, last_day: date) -> list[tuple[str, str, str]]:
    """(id, question, payee) for this week's unexplained transactions, at most three."""
    week = last_day - timedelta(days=6)
    own = [p.lower() for p in rules["rules"].get("own_transfer_payees", [])]
    year, wk, _ = last_day.isocalendar()
    asks = []
    for t in sorted((t for t in m.txs if week <= t.day <= last_day), key=lambda t: -t.amount):
        if t.kind == SPEND and t.unmapped and t.amount >= 20:
            asks.append((f"{t.payee} {money(t.amount)} on {t.day:%a %-d %b} has no category I know. "
                         "What was it?", t.payee))
        elif t.kind == "transfer" and t.amount >= 20 and not any(o in t.payee.lower() for o in own) \
                and not t.payee.lower().startswith(("to ", "from ", "cashback")):
            asks.append((f"{money(t.amount)} to {t.payee} on {t.day:%a %-d %b} counted as a transfer. "
                         "Was it spending? If so, on what?", t.payee))
    return [(f"Q-{year}-{wk}-{i + 1}", q, payee) for i, (q, payee) in enumerate(asks[:3])]


def _routines(agenda: Agenda) -> list[str]:
    """Collapses repeating events into one line each: 'Sam: Swimming (Mon, Wed)'."""
    groups: dict[tuple[str, str], list[str]] = {}
    for e in agenda.today + agenda.upcoming:
        if e.recurring:
            key = (e.people[0] if len(e.people) == 1 else "Family", e.short_title)
            day = f"{e.start:%a}"
            if day not in groups.setdefault(key, []):
                groups[key].append(day)
    return [f"{who}: {title} ({', '.join(days)})" for (who, title), days in groups.items()]


def build_week(settings: dict, last_day: date) -> Week:
    zone = tz(settings)
    rules = finance.load_rules()
    since = datetime.combine(last_day - timedelta(days=7), datetime.min.time(), zone)
    m = finance.build(last_day, since)
    week_spend, typical = _week_numbers(m, rules, last_day)
    rows, on_track = _category_rows(m, rules)
    plan = finance.load_plan()

    lumps = []
    for k in range(3):
        month = (last_day.month - 1 + k) % 12 + 1
        year = last_day.year + (last_day.month - 1 + k) // 12
        for l in plan.lumps_due(month):
            due = date(year, month, 1)
            if k > 0 or due.month == last_day.month:
                lumps.append((l.item, due, l.amount_primary(plan.fx_rate), l.category))
    set_aside = sum(l.amount_primary(plan.fx_rate) / (12 if l.frequency == "Annual" else 60)
                    for l in plan.lines if l.frequency in ("Annual", "5-Year"))

    ahead = None
    try:
        s2 = copy.deepcopy(settings)
        s2["calendar"]["lookahead_days"] = 6
        ahead = load_agenda(s2, last_day + timedelta(days=1), zone)
    except Exception:
        log.exception("weekly: calendar failed")

    first_day = last_day - timedelta(days=6)
    ledger_week: dict[str, dict[str, float]] = {}
    for t in m.txs:
        if t.kind == finance.LEDGER and first_day <= t.day <= last_day:
            by = ledger_week.setdefault(t.ledger, {})
            by[t.payee] = by.get(t.payee, 0.0) + t.amount
    nw_line = ""
    if last_day == datetime.now(zone).date():
        try:
            cfg = settings.get("networth", {})
            now, ch = networth.change_since(last_day, 7)
            reasons = networth.reasons_from(m.txs, ch.since, last_day) if ch else {}
            reasons.update(cfg.get("reasons") or {})
            nw_line = networth.line(now, ch, f"since {ch.since:%-d %B}" if ch else "", cfg.get(
                "notable_weekly", 5000), cfg.get("labels"), reasons)
        except Exception:
            log.exception("weekly: net worth failed")
    return Week(last_day=last_day, networth=nw_line, week_spend=week_spend, typical_week=typical, rows=rows,
                ledger_week={k: sorted(v.items(), key=lambda x: -x[1]) for k, v in ledger_week.items()},
                on_track=on_track, drift=_drift(m, rules, last_day), questions=_questions(m, rules, last_day),
                lumps_60d=lumps, annual_set_aside=set_aside, money=m, ahead=ahead,
                routines=_routines(ahead) if ahead else [])


# ---------- Claude ----------

WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "focus": {"type": "array", "items": {
            "type": "object",
            "properties": {"who": {"type": "string"}, "what": {"type": "string"}},
            "required": ["who", "what"], "additionalProperties": False}},
        "ideas": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "detail": {"type": "string"},
                           "per_month": {"type": ["number", "null"]},
                           "kind": {"type": "string", "enum": ["cut", "restructure", "earn"]}},
            "required": ["title", "detail", "per_month", "kind"], "additionalProperties": False}},
        "project_notes": {"type": "array", "items": {
            "type": "object",
            "properties": {"project": {"type": "string"}, "note": {"type": "string"}},
            "required": ["project", "note"], "additionalProperties": False}},
    },
    "required": ["headline", "focus", "ideas", "project_notes"],
    "additionalProperties": False,
}


def _recent_ideas(last_day: date) -> list[dict]:
    if not IDEAS.exists():
        return []
    cutoff = (last_day - timedelta(days=28)).isoformat()
    return [i for i in map(json.loads, IDEAS.read_text().splitlines()) if i["date"] >= cutoff]


def write(settings: dict, w: Week, ps: list[projects.Project]) -> dict:
    m = w.money
    view = projects.today_view(ps, w.last_day + timedelta(days=1))
    numbers = {
        "currency": primary_code(),
        "week_ending": w.last_day.isoformat(),
        "daily_spending_this_week": round(w.week_spend),
        "daily_spending_typical_week": round(w.typical_week),
        "month": f"{w.last_day:%B}",
        "month_spent": round(m.mtd_total),
        "fixed_monthly_spent": round(sum(e for _, e in m.fixed_paid)),
        "fixed_monthly_plan": round(sum(e for _, e in m.fixed_plan)),
        "daily_spending_month": round(m.day_to_day_mtd),
        "daily_spending_pace": round(m.day_to_day_budget * m.pace_fraction),
        "month_budget": round(m.budget_month),
        "share_of_month_elapsed": round(m.pace_fraction, 2),
        "categories_off_pace": [{"category": r.category, "budget": round(r.budget),
                                 "spent": round(r.spent), "expected_by_now": round(r.expected),
                                 "flag": r.flag} for r in w.rows],
        "no_budget_line": [{"category": c, "spent": round(a)} for c, a in m.unbudgeted],
        "one_offs_this_month": [{"payee": t.payee, "amount": round(t.amount), "why": t.note}
                                for t in m.one_offs_mtd],
        "savings_draw_this_month": round(m.draw_mtd),
        "rental_income_this_month": round(m.rental_mtd),
        "ledgers_kept_out_of_household_numbers": [
            {"ledger": led.label, "this_month": round(led.month), "monthly_budget": round(led.budget) or None,
             "costs_in_window": round(led.costs), "reimbursed": round(led.reimbursed),
             "this_week": [{"payee": p, "amount": round(e)} for p, e in w.ledger_week.get(led.label, [])]}
            for led in m.ledgers],
        "managed_account_fees_this_month": round(m.managed_fees_mtd),
        "managed_account_fees_per_year": round(m.managed_fee_rate * m.managed_balance),
        "net_worth_line": w.networth,
        "lumps_next_60_days": [{"item": i, "category": c, "month": f"{d:%B %Y}", "amount": round(e)}
                               for i, d, e, c in w.lumps_60d],
        "rental_income_last_received": next(
            ({"date": str(t.day), "amount": round(-t.amount)} for t in sorted(
                (t for t in m.txs if t.kind == finance.RENTAL), key=lambda t: t.day, reverse=True)), None),
        "annual_set_aside_per_month": round(w.annual_set_aside),
        "off_book": [{"item": i, "per_month": round(e)} for i, e, _ in m.off_book],
        "drift": [{"payee": d.payee, "last_4_weeks": round(d.last4),
                   "previous_4_week_average": round(d.before), "note": d.note} for d in w.drift],
    }
    week_ahead = [{"when": f"{e.start:%a %-d} {e.time_label()}", "who": e.people or ["Family"],
                   "what": e.title} for e in (w.ahead.today + w.ahead.upcoming if w.ahead else [])
                  if not e.recurring]
    project_state = [{"project": p.name, "summary": p.summary,
                      "steps": [{"do": s.do, "who": s.who, "status": s.status,
                                 "last_done": str(s.last_done) if s.last_done else None,
                                 "after": s.after, "not_before": str(s.not_before) if s.not_before else None,
                                 "waiting_on": s.waiting_on or None,
                                 "updates": [f"{u['date']} {u['who']}: {u['text']}"
                                             for u in projects.updates(s.id)[-3:]]}
                                for s in p.steps]} for p in ps]
    next_up = [{"who": s.who, "do": s.do, "project": s.project, "priority": s.priority}
               for s in view.actionable + view.checks_due]
    notes_path = ROOT / settings["household"].get("notes_file", "data/household_notes.md")
    system = (
        f"You write the weekly household letter for {' and '.join(people(settings))}. It goes out "
        f"on {w.last_day:%A} {_part_of_day(settings['email'].get('weekly_hour', 17))}. "
        f"About them: {settings['household']['about']}\n\n"
        f"Household notes:\n{notes_path.read_text() if notes_path.exists() else ''}"
        + llm._feedback()
        + "\n\nVoice: direct, warm, specific, addressed to both of them. Plain English. "
          f"Amounts are in {primary_code()}; round to whole units. Never moralize about dining out or hobbies; they budgeted for "
          "them. Never do arithmetic yourself: use only the numbers you're given. State only "
          "facts that appear in the numbers, calendar, projects or notes; never invent details "
          "(amounts, schedules, reasons) and never guess why something happened. Keep weekly "
          "and monthly figures apart: a pace figure is month-to-date, not a weekly amount."
    )
    prompt = f"""Write these parts of this {w.last_day:%A}'s letter.

headline: two or three sentences. How the week's daily spending compares with a typical
week, how fixed monthly expenses and daily spending each stand against plan (keep the two
separate; call them "fixed monthly expenses" and "daily spending"), and the one thing most
worth knowing. If nothing is remarkable, say so in one sentence.

focus: three to five things to get done this coming week, chosen from "next_up" and the
week ahead. Each: who ({", ".join(people(settings))} or Both) and what, in one short sentence. Prefer
high-priority steps and anything the calendar makes timely.

ideas: up to three concrete ways to save or earn, each with a rough amount per month (or null)
and what it would take. Mix cut / restructure / earn. Skip anything already suggested
recently (listed below) unless something changed. Fewer, better ideas beat filler.

project_notes: one sentence per project on where it stands and what's stuck. The steps'
"updates" are the household's latest word and override anything older; use them.

Numbers (computed in Python):
{json.dumps(numbers, ensure_ascii=False)}

Week ahead (one-off events; routines are listed separately):
{json.dumps(week_ahead, ensure_ascii=False)}
Routines: {json.dumps(w.routines, ensure_ascii=False)}

Next up across projects:
{json.dumps(next_up, ensure_ascii=False)}

Projects:
{json.dumps(project_state, ensure_ascii=False)}

Ideas suggested in the last four weeks:
{json.dumps([i['title'] for i in _recent_ideas(w.last_day)], ensure_ascii=False)}"""
    s2 = copy.deepcopy(settings)
    s2["llm"]["effort"] = "high"
    return llm._ask_json(s2, system, prompt, WRITE_SCHEMA)


# ---------- rendering ----------



def sections(w: Week, words: dict) -> list[tuple[str, list[str]]]:
    """The letter as (heading, paragraphs). Shared by HTML, text and the saved report."""
    m = w.money
    out = [("This week", [words["headline"]])]

    from .render import drawer_lines, split_lines
    para = ([w.networth] if w.networth else []) + [
        f"Daily spending this week: {money(w.week_spend)}, against a typical "
        f"{money(w.typical_week)}."] + split_lines(m)
    for r in w.rows:
        para.append(f"{r.category}: {money(r.spent)} spent, ahead of the {money(r.expected)} "
                    f"expected by now (month budget {money(r.budget)}).")
    if w.on_track:
        para.append("On track: " + ", ".join(w.on_track) + ".")
    if m.unbudgeted:
        para.append("No budget line: " + ", ".join(f"{c} {money(a)}" for c, a in m.unbudgeted) + ".")
    if m.managed_fees_mtd or m.managed_balance:
        yearly = m.managed_fee_rate * m.managed_balance
        para.append(f"{m.managed_fees_label}: {money(m.managed_fees_mtd)} taken this month; at "
                    f"{m.managed_fee_rate:.2%} of the {money(m.managed_balance)} managed, about {money(yearly)} "
                    f"a year ({money(yearly / 12)} a month). Paid from the investments, so not in the household budget.")
    if m.cash_topups_8w or m.cash_payments_8w:
        other = m.cash_topups_8w - m.cash_payments_8w
        label = m.cash_payment_label or "recurring cash payments"
        para.append(
            f"Cash, last 8 weeks: {money(m.cash_topups_8w)} withdrawn, {money(m.cash_payments_8w)} for {label}. "
            + (f"Other cash spending: about {money(other)}, give or take what's in the drawer." if other >= 0 else
               f"Withdrawals fell {money(-other)} short of {label}, so some of it came from elsewhere."))
    para += drawer_lines(m)
    out.append(("Money", para))

    for led in m.ledgers:
        week = w.ledger_week.get(led.label, [])
        if not (week or led.month or led.costs or led.budget):
            continue
        week_total = sum(e for _, e in week)
        items = ", ".join(f"{p} {money(e)}" if e >= 0 else f"{p} {money(-e)} back" for p, e in week)
        head = (f"This week: {money(week_total)}" if week_total >= 0
                else f"This week: a net {money(-week_total)} back") if week else "Nothing new this week"
        tail = (f"So far: {money(led.costs)} spent, {money(led.reimbursed)} reimbursed, "
                f"net {money(led.costs - led.reimbursed)}." if led.reimbursed else
                f"{w.last_day:%B} so far: {money(led.month)}"
                + (f" of the {money(led.budget)} monthly budget." if led.budget else "."))
        out.append((led.label, [head + (f" ({items})" if items else "") + ". " + tail
                                + " Kept out of the household numbers."]))

    from . import headsup, practice
    coming = [headsup.line(i) for i in headsup.items(w.last_day, 60)]
    coming += [f"{i}: {money(e)}, due {d:%B}." for i, d, e, _ in w.lumps_60d]
    coming.append(f"Annual and five-year costs, smoothed, call for setting aside "
                  f"{money(w.annual_set_aside)} a month.")
    if m.draw_mtd:
        coming.append(f"Savings draw this month so far: {money(m.draw_mtd)}.")
    if m.savings_balance and m.draw_monthly:
        coming.append(f"Runway: savings ({money(m.savings_balance)}) cover about "
                      f"{m.savings_balance / m.draw_monthly:.0f} months at the recent draw.")
    out.append(("Coming up (next 60 days)", coming))

    if w.drift:
        out.append(("Drift watch", [
            f"{d.payee}: {money(d.last4)} in the last 4 weeks"
            + (f", up from {money(d.before)}" if d.before else "")
            + (f" ({d.note})" if d.note else "") + "." for d in w.drift]))

    ahead = [f"{f['who']}: {f['what']}" for f in words["focus"]] + [x for x in [practice.summary(w.last_day)] if x]
    if w.ahead:
        for e in w.ahead.today + w.ahead.upcoming:
            if not e.recurring:
                who = e.people[0] if len(e.people) == 1 else "Family"
                ahead.append(f"{e.start:%a %-d} {e.time_label()}, {who}: {e.short_title}")
        if w.routines:
            ahead.append("Routines: " + "; ".join(w.routines) + ".")
    out.append(("The week ahead", ahead))

    if words["ideas"]:
        out.append(("Save or earn", [
            f"{i['title']}"
            + (f" (about {money(i['per_month'])} a month)" if i["per_month"] else "")
            + f". {i['detail']}" for i in words["ideas"]]))
    if w.questions:
        out.append(("Questions for you (reply with the id and your answer)",
                    [f"{qid}: {q}" for qid, q, _ in w.questions]))
    out.append(("Projects", [f"{n['project']}: {n['note']}" for n in words["project_notes"]]))
    return out


def to_html(w: Week, secs: list[tuple[str, list[str]]], image_src=lambda n: f"cid:{n}",
            banner: str = "") -> str:
    from html import escape

    from .render import EMAIL_HEAD, EMAIL_TAIL, INK, MUTED, PAPER, RULE, _h2, one_off_note, portal_button
    parts = [EMAIL_HEAD,
             f'<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
             f'color:{INK};background:{PAPER};max-width:640px;margin:0 auto;padding:16px;'
             f'font-size:15px;line-height:1.5">',
             '<h1 style="font-size:22px;margin:0 0 4px">Your week</h1>',
             f'<div style="color:{MUTED}">Week ending {w.last_day:%A} {w.last_day.day} {w.last_day:%B %Y}</div>',
             portal_button(portal_url())]
    if banner:
        parts.append(f'<p style="margin:10px 0;padding:8px 12px;border-radius:8px;background:#fdf3e7;'
                     f'color:{INK};font-size:14px">{escape(banner)}</p>')
    for heading, paras in secs:
        parts.append(_h2(heading))
        if heading in ("The week ahead", "Drift watch", "Save or earn",
                       "Questions for you (reply with the id and your answer)"):
            parts.append('<ul style="margin:0 0 0 18px;padding:0">'
                         + "".join(f'<li style="margin:3px 0">{escape(p)}</li>' for p in paras) + "</ul>")
        else:
            parts += [f'<p style="margin:0 0 8px">{escape(p)}</p>' for p in paras]
        if heading == "Money":
            parts.append(f'<img src="{image_src("mtd")}" alt="Chart: spending so far this month" '
                         'width="600" style="display:block;width:100%;max-width:600px;height:auto;margin:6px 0 4px">')
            note = one_off_note(w.money)
            if note:
                parts.append(f'<p style="margin:0 0 12px;color:{MUTED};font-size:13px">{escape(note)}</p>')
            parts.append(f'<img src="{image_src("categories")}" alt="Chart: daily spending by category against budget" '
                         'width="600" style="display:block;width:100%;max-width:600px;height:auto;margin:6px 0 12px">')
    parts.append(f'<p style="margin-top:32px;color:{MUTED};font-size:12px;border-top:1px solid {RULE};'
                 f'padding-top:10px">{escape(brand())}, {w.last_day:%A} letter. Reply to answer a question, add '
                 'something to the week, or say what to change.</p></div>')
    parts.append(EMAIL_TAIL)
    return "\n".join(parts)


def to_text(w: Week, secs: list[tuple[str, list[str]]], banner: str = "") -> str:
    from .render import one_off_note
    lines = [f"Your week. Week ending {w.last_day:%A} {w.last_day.day} {w.last_day:%B %Y}",
             f"Open {brand()}: {portal_url()}" if portal_url() else "", ""]
    if banner:
        lines += [banner, ""]
    for heading, paras in secs:
        lines += [heading.upper()] + [f"  {p}" for p in paras]
        if heading == "Money" and one_off_note(w.money):
            lines.append(f"  {one_off_note(w.money)}")
        lines.append("")
    return "\n".join(lines)


# ---------- run ----------

def preview_to_portal(settings: dict, last_day: date) -> str:
    """Builds the letter for `last_day` into out/ (where the portal's Week page reads it) with a
    Preview banner. The real letter that day writes the same file, replacing it."""
    ps = projects.load()
    w = build_week(settings, last_day)
    secs = sections(w, write(settings, w, ps))
    year, wk, _ = last_day.isocalendar()
    stem = f"weekly-{year}-{wk:02d}"
    imgs = {"mtd": charts.month_to_date(w.money)}
    cats = charts.categories(w.money, finance.load_rules()["rules"]["day_to_day"])
    if cats:
        imgs["categories"] = cats
    for name, png in imgs.items():
        (ROOT / "out" / f"{stem}-{name}.png").write_bytes(png)
    banner = (f"Preview, built {datetime.now(tz(settings)):%a %-d %b %H:%M} from today's data. "
              f"The real letter goes out {last_day:%A %-d %B} at {settings['email'].get('weekly_hour', 17)}:00 "
              "and replaces this page.")
    (ROOT / "out" / f"{stem}.html").write_text(to_html(w, secs, lambda n: f"{stem}-{n}.png", banner))
    (ROOT / "out" / f"{stem}.txt").write_text(to_text(w, secs))
    return stem


def run(settings: dict, last_day: date, dry_run: bool, late: str = "") -> None:
    """Builds the letter for the week ending `last_day` and sends it. `late` is a line saying it
    comes late (tick.py's catch-up), shown at the top."""
    ps = projects.load()
    w = build_week(settings, last_day)
    words = write(settings, w, ps)
    secs = sections(w, words)
    year, wk, _ = last_day.isocalendar()
    imgs = {"mtd": charts.month_to_date(w.money)}
    cats = charts.categories(w.money, finance.load_rules()["rules"]["day_to_day"])
    if cats:
        imgs["categories"] = cats
    stem = f"weekly-{year}-{wk:02d}"
    folder = ROOT / "out" / ("preview" if dry_run else "")
    folder.mkdir(parents=True, exist_ok=True)
    for name, png in imgs.items():
        (folder / f"{stem}-{name}.png").write_bytes(png)
    (folder / f"{stem}.html").write_text(to_html(w, secs, lambda n: f"{stem}-{n}.png", late))
    text = to_text(w, secs, late)
    (folder / f"{stem}.txt").write_text(text)
    if dry_run:
        log.info("weekly dry run: wrote out/preview/%s.html", stem)
        return
    mailer.send_or_queue(settings, stem, f"{brand(settings)} · your week ahead, {last_day + timedelta(days=1):%-d %B}",
                         text, to_html(w, secs, banner=late), images=imgs)
    asked = json.loads(QUESTIONS.read_text()) if QUESTIONS.exists() else {}
    asked.update({qid: {"payee": payee, "text": q, "asked": last_day.isoformat()}
                  for qid, q, payee in w.questions})
    QUESTIONS.write_text(json.dumps(asked, indent=1, ensure_ascii=False))
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"{year}-{wk:02d}.md").write_text(text)
    IDEAS.parent.mkdir(exist_ok=True)
    with IDEAS.open("a") as f:
        for i in words["ideas"]:
            f.write(json.dumps({"date": last_day.isoformat(), "title": i["title"], "kind": i["kind"],
                                "per_month": i["per_month"], "acted": None}) + "\n")
    log.info("weekly: sent %s", stem)


def main() -> None:
    parser = argparse.ArgumentParser(description="The weekly letter")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date", type=date.fromisoformat, help="the letter's day (YYYY-MM-DD); default: the next email.weekly_day")
    args = parser.parse_args()
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "out").mkdir(exist_ok=True)
    logging.basicConfig(filename=ROOT / "logs" / "butler.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger().addHandler(logging.StreamHandler())
    settings = load_settings()
    today = datetime.now(tz(settings)).date()
    last_day = args.date or next_letter_day(settings, today)
    run(settings, last_day, args.dry_run)


if __name__ == "__main__":
    main()
