"""Turns the brief's parts into an HTML email and a plain-text twin."""

import re
from dataclasses import dataclass, field
from datetime import date
from html import escape

from .agenda import OUTSIDE_MARK, Agenda, Event, is_outside
from .config import brand, people as adults
from .fmt import money, primary_code, scale, second_code
from .finance import Summary
from .projects import Today as ProjectsToday


@dataclass
class Brief:
    day: date
    agenda: Agenda | None
    prep: list[dict]
    news: list[dict]
    region_labels: dict[str, str]
    people: list[dict] = field(default_factory=list)
    money: Summary | None = None
    networth: str = ""
    portal_url: str = ""
    banner: str = ""        # a note at the top, e.g. for a rebuilt page
    todos: list[dict] = field(default_factory=list)   # open items in the household Reminders list
    headsup: list[str] = field(default_factory=list)  # deadlines and expiring documents
    weather: str = ""
    phrase: dict | None = None                        # {phrase, translation, why}, for language learning
    phrase_label: str = ""                            # e.g. "Spanish for today"
    practice: str = ""
    next_up: ProjectsToday | None = None
    charts: dict[str, bytes] = field(default_factory=dict)   # name -> PNG, shown in Money
    problems: list[str] = field(default_factory=list)
    not_set_up: list[str] = field(default_factory=list)  # parts still to connect, e.g. "money"
    updates: list[str] = field(default_factory=list)     # "Bento updated overnight to v0.2.0. …"

    @property
    def subject(self) -> str:
        return f"{brand()} · {self.day:%A} {self.day.day} {self.day:%B}"


INK, MUTED, RULE, ACCENT, PAPER = "#1f2328", "#656d76", "#e5e7eb", "#9a3412", "#fcfcfb"


def email_head(title: str) -> str:
    """The emails' <head>: English, titled (the brief also opens as a page in the browser), and
    pinned to light. Its colors (and the PNG charts) are designed for a light page, and mail apps
    in dark mode otherwise put this dark text on a dark background."""
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{escape(title)}</title>'
            '<meta name="color-scheme" content="light only">'
            '<meta name="supported-color-schemes" content="light only"></head>'
            f'<body style="margin:0;background:{PAPER}">')


EMAIL_TAIL = "</body></html>"


def _luminance(color: str) -> float:
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """The WCAG contrast ratio of two #rrggbb colors, from 1 to 21."""
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def readable(color: str, on: str = PAPER) -> str:
    """`color` as text on `on`: darkened in small steps until it reaches 4.5:1 (WCAG AA), so a
    light person color such as Google's Flamingo stays legible. Anything but #rrggbb is kept."""
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color or ""):
        return color
    color = color.lower()
    while contrast(color, on) < 4.5 and color != "#000000":
        color = "#" + "".join(f"{int(int(color[i:i + 2], 16) * 0.9):02x}" for i in (1, 3, 5))
    return color


def _h2(text: str) -> str:
    return (f'<h2 style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;'
            f'color:{ACCENT};margin:28px 0 8px;border-bottom:1px solid {RULE};'
            f'padding-bottom:6px">{escape(text)}</h2>')


FAMILY = "Family"


def _by_person(brief: Brief, events: list[Event]) -> list[tuple[str, list[Event]]]:
    """Each person's own events, then shared or unassigned ones under Family."""
    groups = [(p["name"], [e for e in events if e.people == [p["name"]]]) for p in brief.people]
    shared = [e for e in events if len(e.people) != 1]
    return groups + ([(FAMILY, shared)] if shared else [])


def _color(brief: Brief, name: str) -> str:
    return next((readable(p["color"]) for p in brief.people if p["name"] == name), MUTED)


def _tag(brief: Brief, e: Event) -> str:
    if len(e.people) != 1:
        return ""
    return (f'<span style="color:{_color(brief, e.people[0])};font-weight:600">'
            f'{escape(e.people[0])}</span> ')


def _outside(e: Event, html: bool = True) -> str:
    """The quiet marker on an invite from outside the household (agenda.from_outside)."""
    if not is_outside(e):
        return ""
    return f'<span style="color:{MUTED}">{escape(OUTSIDE_MARK)}</span>' if html else OUTSIDE_MARK


def _event_row(e: Event, show_day: bool = False, tag: str = "", title: str | None = None) -> str:
    when = f"{e.start:%a} {e.start.day} " if show_day else ""
    where = f'<span style="color:{MUTED}"> · {escape(e.location)}</span>' if e.location else ""
    return (f'<tr><td style="padding:3px 12px 3px 0;color:{MUTED};white-space:nowrap;'
            f'vertical-align:top">{when}{e.time_label()}</td>'
            f'<td style="padding:3px 0">{tag}{escape(title or e.title)}{_outside(e)}{where}</td></tr>')


def _todo_text(t: dict, day: date) -> str:
    due = ""
    if t.get("due"):
        d = date.fromisoformat(t["due"])
        due = (" (overdue since " if d < day else " (due ") + f"{d:%a} {d.day} {d:%b})" if d != day else " (due today)"
    return t["title"] + due


def _todo_html(brief: Brief) -> str:
    items = "".join(f'<li style="margin:3px 0">{escape(_todo_text(t, brief.day))}</li>' for t in brief.todos)
    return (_h2("To do") + f'<ul style="margin:0 0 0 18px;padding:0">{items}</ul>'
            f'<p style="margin:4px 0 0;color:{MUTED};font-size:13px">From your household list in Reminders. '
            f'Tick them off there, in the portal, or reply "done: …".</p>')


def _today_html(brief: Brief) -> str:
    rows = []
    for name, events in _by_person(brief, brief.agenda.today):
        label = (f'<div style="font-weight:600;color:{_color(brief, name)};'
                 f'margin:10px 0 2px">{escape(name)}</div>')
        if events:
            body = ('<table style="border-collapse:collapse">'
                    + "".join(_event_row(e, title=e.short_title if name != FAMILY else e.title)
                              for e in events)
                    + "</table>")
        else:
            body = f'<div style="color:{MUTED}">Nothing on the calendar</div>'
        rows.append(label + body)
    return "".join(rows)


def _holiday_line(agenda: Agenda, day: date) -> str:
    names = agenda.holidays.get(day)
    return " · ".join(names) if names else ""


def _upcoming_holidays(brief: Brief) -> list[tuple[date, list[str]]]:
    return [(d, n) for d, n in sorted(brief.agenda.holidays.items()) if d > brief.day]


def _holiday_summary(holidays: list[tuple[date, list[str]]]) -> str:
    return " · ".join(f"{d:%a} {d.day}: {', '.join(n)}" for d, n in holidays)


def _week(brief: Brief) -> list[Event]:
    # Recurring events appear on their own day; the week view keeps the one-offs.
    return [e for e in brief.agenda.upcoming if not e.recurring]




def _ordinal(n: int) -> str:
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def one_off_note(m: Summary) -> str:
    """Names every one-off left out of the pace chart, so nothing is hidden."""
    def item(t) -> str:
        return f"{t.payee} {money(t.amount)}" + (f" ({t.note})" if t.note else "")
    parts = []
    if m.one_offs_mtd:
        parts.append(f"{m.today:%B}: " + "; ".join(item(t) for t in m.one_offs_mtd))
    if m.one_offs_last_month:
        last = m.one_offs_last_month[0].day
        parts.append(f"{last:%B}: " + "; ".join(item(t) for t in m.one_offs_last_month))
    return ("* Not in the chart: one-off spending. " + ". ".join(parts) + ".") if parts else ""


def _lump(item: str, amount: float) -> str:
    """'Property tax (€5,084), paid from Rental checking ($1,401 now)'."""
    from . import networth
    from .config import load_settings
    from .finance import load_rules
    funding = load_rules()["rules"].get("lump_funding") or {}
    account = next((acct for key, acct in funding.items() if item.startswith(key)), None)
    snaps = networth.load()
    if not account or not snaps:
        return f"{item} ({money(amount)})"
    row = next((a for a in snaps[-1]["accounts"] if a["name"] == account), None)
    if not row:
        return f"{item} ({money(amount)})"
    from .fmt import second_money
    label = load_settings().get("networth", {}).get("labels", {}).get(account, account)
    native = second_money(row["balance"]) if row["currency"] == second_code().lower() else money(row["balance"])
    return f"{item} ({money(amount)}), paid from {label} ({native} now)"


def one_offs_text(txs) -> str:
    """One-offs combined by payee: 'Hardware store €20 (shelf brackets); …'."""
    by: dict[str, list] = {}
    for t in txs:
        e = by.setdefault(t.payee, [0.0, t.note])
        e[0] += t.amount
    return "; ".join(f"{p} {money(v)}" + (f" ({n})" if n else "") for p, (v, n) in by.items() if abs(v) >= 1)


def split_lines(m: Summary) -> list[str]:
    """Fixed monthly expenses against their plan, then daily spending against pace."""
    fixed_paid = sum(e for _, e in m.fixed_paid)
    fixed_plan = sum(e for _, e in m.fixed_plan)
    paid = ", ".join(f"{n} {money(e)}" for n, e in m.fixed_paid) or "nothing yet"
    still = ", ".join(f"{n} {money(e)}" for n, e in m.fixed_remaining) or "nothing; all paid"
    month_over = m.pace_fraction >= 1
    pace = m.day_to_day_budget * m.pace_fraction
    gap = m.day_to_day_mtd - pace
    verdict = ("right on pace" if abs(gap) < 25 * scale()
               else f"{money(abs(gap))} {'over' if gap > 0 else 'under'} pace")
    lines = [
        f"Fixed monthly expenses: {money(fixed_paid)} paid of a {money(fixed_plan)} plan. "
        f"Paid: {paid}. {'Not paid against the plan' if month_over else 'Still to come'}: {still}.",
        (f"Daily spending: {money(m.day_to_day_mtd)} against a {money(m.day_to_day_budget)} budget "
         f"({verdict.replace('pace', 'budget')})." if month_over else
         f"Daily spending: {money(m.day_to_day_mtd)} against {money(pace)} pace for the "
         f"{m.today.day}{_ordinal(m.today.day)} ({verdict}; {money(m.day_to_day_budget)} for the month)."),
    ]
    if m.one_offs_total >= 1:
        # One line that names them: a separate total and list said the same thing twice.
        names = one_offs_text(m.one_offs_mtd)
        payees = {t.payee for t in m.one_offs_mtd}
        lines.append("One-offs (neither fixed nor daily): "
                     + (f"{names}." if len(payees) == 1 else f"{money(m.one_offs_total)} in all. {names}."))
    return lines


def trend_text(m: Summary) -> str:
    """', down from a €80 weekly average over the previous 4 weeks', compared in whole units as
    shown. Empty when there's less than a week of history before the last seven days."""
    if not m.prior_days:
        return ""
    now, before = round(m.last7), round(m.prior_weekly_avg)
    trend = "the same as" if now == before else "down from" if now < before else "up from"
    span = "the previous 4 weeks" if m.prior_days == 28 else f"the {m.prior_days} days before"
    return f", {trend} a {money(m.prior_weekly_avg)} weekly average over {span}"


def money_lines(m: Summary) -> list[str]:
    """The Money section as plain sentences, shared by the HTML and text emails."""
    lines = []
    if m.new:
        top = ", ".join(f"{t.payee} {money(t.amount)}" for t in m.new[:3])
        noun = "purchase" if len(m.new) == 1 else "purchases"
        lines.append(f"New since the last brief: {money(sum(t.amount for t in m.new))} across "
                     f"{len(m.new)} {noun}. Biggest: {top}.")
    else:
        lines.append("No new spending has posted since the last brief.")
    lines += split_lines(m)
    if m.over_pace:
        lines.append("Daily categories running over: " + "; ".join(
            f"{c} {money(a)} vs {money(e)}" for c, a, e in m.over_pace[:2]) + ".")
    if m.unbudgeted:
        lines.append("No budget line yet: " + ", ".join(
            f"{c} {money(a)}" for c, a in m.unbudgeted) + ".")
    if m.savings_balance and m.draw_monthly:
        lines.append(f"Runway: savings ({money(m.savings_balance)}) cover about "
                     f"{m.savings_balance / m.draw_monthly:.0f} months at the recent draw of "
                     f"{money(m.draw_monthly)} a month.")
    lines.append(f"Last 7 days: {money(m.last7)} of daily spending" + trend_text(m) + ".")
    flows = []
    if m.draw_mtd:
        flows.append(f"savings draw {money(m.draw_mtd)}")
    if m.rental_mtd:
        flows.append(f"rental income {money(m.rental_mtd)}")
    if m.funding_mtd:
        flows.append(f"moved to the cash account {money(m.funding_mtd)}")
    if flows:
        lines.append("This month: " + "; ".join(flows) + ".")
    if m.lumps_due:
        lines.append("Due this month: " + "; ".join(_lump(i, a) for i, a in m.lumps_due) + ".")
    lines += drawer_lines(m)
    if m.managed_fees_mtd or m.managed_balance:
        yearly = m.managed_fee_rate * m.managed_balance
        lines.append(f"{m.managed_fees_label}: {money(m.managed_fees_mtd)} taken this month. "
                     f"At {m.managed_fee_rate:.2%} of the {money(m.managed_balance)} managed, that's about "
                     f"{money(yearly)} a year ({money(yearly / 12)} a month), paid from the investments.")
    for led in m.ledgers:
        if not (led.month or led.budget or led.costs):
            continue
        if led.reimbursed:
            lines.append(f"{led.label} (kept out of the household numbers): {money(led.costs)} spent, "
                         f"{money(led.reimbursed)} reimbursed so far, net {money(led.costs - led.reimbursed)}.")
        else:
            of = f" of its {money(led.budget)} budget" if led.budget else ""
            lines.append(f"{led.label} (kept out of the household numbers): {money(led.month)} this month{of}.")
    left_out = [f"{item} {money(amount)}/month, paid {how or 'outside these accounts'}"
                for item, amount, how in m.off_book]
    if left_out:
        lines.append("Not in these numbers: " + "; ".join(left_out) + ".")
    return lines


def drawer_lines(m: Summary) -> list[str]:
    """The cash drawer estimate, with a nudge to withdraw cash before a recurring cash payment."""
    from .finance import WEEKDAYS, load_rules
    drawer = load_rules().get("cash_drawer") or {}
    rec = drawer.get("recurring") or {}
    if m.drawer is not None:
        less = f", less {rec['label']}" if rec else ""
        if m.drawer < 0:
            # More went out than the count plus the withdrawals Butler saw: a withdrawal it
            # didn't recognise, or a stale count. A negative "at most" would read as a bug.
            paid = rec["label"] if rec else "cash payments"
            line = (f"Cash drawer: needs a count. More went to {paid} since {m.drawer_counted:%-d %b} than "
                    f"that count plus the withdrawals I saw, so one may be missing. Count the drawer and "
                    f"update cash_drawer in category_map.yaml, or ask Claude Code to.")
        else:
            line = (f"Cash drawer: about {money(m.drawer)} at most (counted {m.drawer_counted:%-d %b}, "
                    f"plus top-ups{less}).")
        if rec:
            payday = WEEKDAYS[rec.get("weekday", "friday").lower()]
            days = (payday - m.today.weekday()) % 7
            counted_after_paying = days == 0 and m.drawer_counted == m.today
            if days <= 1 and m.drawer < rec["amount"] and not counted_after_paying:
                who = f"{drawer['topup_person']}, " if drawer.get("topup_person") else ""
                line += (f" {rec['label']} ({money(rec['amount'])}) is due {'today' if days == 0 else 'tomorrow'}: "
                         f"{who}withdraw cash.")
        return [line]
    if m.cash_topups_mtd or m.cash_payments_8w:
        paid = f" {m.cash_payment_label} is paid from the drawer and counted above." if m.cash_payment_label else ""
        return [f"Cash: {money(m.cash_topups_mtd)} withdrawn this month.{paid}"]
    return []


NEXT_UP_PER_PERSON = 3
COUNTDOWN_DAYS = 120


def next_up_groups(view: ProjectsToday) -> list[tuple[str, list[str], int]]:
    """(who, lines, how many more) for each adult, then shared steps."""
    groups = []
    for who in adults() + ["Both"]:
        steps = [s for s in view.actionable if s.who == who]
        checks = [s for s in view.checks_due if s.who == who]
        chase = [s for s in view.to_chase if s.who == who]
        daily = [s for s in checks if s.every == 1]   # daily habits are always for today: list first
        lines = ([f"Every day: {s.do} ({s.project})" for s in daily]
                 + [f"{s.do} ({s.project})" for s in steps]
                 + [f"Check: {s.do} ({s.project})" for s in checks if s.every != 1]
                 + [f"Chase {s.waiting_on}: {s.do} ({s.project})" for s in chase])
        if lines:
            label = "Together" if who == "Both" else who
            groups.append((label, lines[:NEXT_UP_PER_PERSON], max(0, len(lines) - NEXT_UP_PER_PERSON)))
    return groups


def countdown_lines(view: ProjectsToday) -> list[str]:
    return [f"{s.do} ({s.project}): opens in {d} days, on {s.not_before:%-d %B %Y}"
            for s, d in view.countdowns if d <= COUNTDOWN_DAYS]


def portal_button(url: str) -> str:
    """A prominent link to the portal, for the top of the emails."""
    if not url:
        return ""
    host = url.split("//", 1)[-1].rstrip("/")
    return (f'<p style="margin:12px 0 4px"><a href="{escape(url, quote=True)}" '
            f'style="display:inline-block;background:{ACCENT};color:#ffffff;text-decoration:none;'
            f'font-weight:600;padding:8px 14px;border-radius:8px">Open {escape(brand())} · {escape(host)} →</a></p>')


def to_html(brief: Brief, image_src=lambda name: f"cid:{name}") -> str:
    """image_src maps a chart name to its <img> src: a cid: for email, a file for previews."""
    parts = [
        email_head(brief.subject),
        f'<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        f'color:{INK};background:{PAPER};max-width:640px;margin:0 auto;padding:16px;'
        f'font-size:15px;line-height:1.5">',
        '<h1 style="font-size:22px;margin:0 0 4px">Good morning</h1>',
        f'<div style="color:{MUTED}">{brief.day:%A} {brief.day.day} {brief.day:%B %Y}</div>',
        portal_button(brief.portal_url),
    ]
    if brief.banner:
        parts.append(f'<p style="margin:10px 0;padding:8px 12px;border-radius:8px;background:#fdf3e7;'
                     f'color:{INK};font-size:14px">{escape(brief.banner)}</p>')

    if brief.weather:
        parts.append(f'<p style="margin:10px 0 0;color:{MUTED}">{escape(brief.weather)}</p>')
    if brief.headsup:
        parts.append(_h2("Heads up") + '<ul style="margin:0 0 0 18px;padding:0">'
                     + "".join(f'<li style="margin:3px 0">{escape(h)}</li>' for h in brief.headsup) + "</ul>")
    if brief.todos:
        parts.append(_todo_html(brief))

    if brief.agenda:
        parts.append(_h2("Today"))
        holiday = _holiday_line(brief.agenda, brief.day)
        if holiday:
            parts.append(f'<p style="margin:0 0 6px;color:{ACCENT}">{escape(holiday)}</p>')
        parts.append(_today_html(brief))

        if brief.prep:
            parts.append(_h2("Get ready for"))
            for p in brief.prep:
                e = p["event"]
                parts.append(
                    f'<p style="margin:0 0 10px"><strong>{escape(e.title)}</strong>{_outside(e)}'
                    f'<span style="color:{MUTED}"> · {e.start:%a %d %b}</span><br>'
                    f'{escape(p["note"])}</p>')

        week = _week(brief)
        holidays = _upcoming_holidays(brief)
        if week or holidays:
            parts.append(_h2("This week"))
            if holidays:
                parts.append(f'<p style="margin:0 0 6px;color:{MUTED}">'
                             + escape(_holiday_summary(holidays))
                             + "</p>")
            if week:
                parts.append('<table style="border-collapse:collapse">'
                             + "".join(_event_row(e, show_day=True, tag=_tag(brief, e),
                                                  title=e.short_title if len(e.people) == 1 else e.title)
                                       for e in week)
                             + "</table>")

    if brief.next_up:
        parts.append(_h2("Next up"))
        for who, lines, more in next_up_groups(brief.next_up):
            color = _color(brief, who) if who != "Together" else MUTED
            parts.append(f'<div style="font-weight:600;color:{color};margin:10px 0 2px">{escape(who)}</div>')
            parts.append('<ul style="margin:0 0 0 18px;padding:0">'
                         + "".join(f'<li style="margin:2px 0">{escape(l)}</li>' for l in lines)
                         + "</ul>")
            if more:
                parts.append(f'<div style="color:{MUTED};font-size:13px;margin-left:18px">and {more} more</div>')
        for l in countdown_lines(brief.next_up):
            parts.append(f'<p style="margin:8px 0 0;color:{ACCENT}">{escape(l)}</p>')
        if brief.practice:
            parts.append(f'<p style="margin:8px 0 0">{escape(brief.practice)}</p>')
    if brief.phrase:
        sp = brief.phrase
        parts.append(_h2(brief.phrase_label) + f'<p style="margin:0"><strong>{escape(sp["phrase"])}</strong>: '
                     f'{escape(sp["translation"])} <span style="color:{MUTED}">{escape(sp["why"])}</span></p>')

    if brief.money:
        parts.append(_h2("Money"))
        lines = money_lines(brief.money)
        for i, l in enumerate(brief.networth.splitlines()):
            weight = "font-weight:600;" if i == 0 else ""
            parts.append(f'<p style="margin:0 0 8px;{weight}">{escape(l)}</p>')
        parts += [f'<p style="margin:0 0 8px">{escape(l)}</p>' for l in lines[:3]]
        for name, alt in (("mtd", "Chart: spending so far this month against the budget pace and last month"),
                          ("categories", "Chart: daily spending by category, spent against the month's budget")):
            if name in brief.charts:
                parts.append(f'<img src="{image_src(name)}" alt="{alt}" width="600" '
                             f'style="display:block;width:100%;max-width:600px;height:auto;margin:6px 0 4px">')
                note = one_off_note(brief.money) if name == "mtd" else ""
                if note:
                    parts.append(f'<p style="margin:0 0 12px;color:{MUTED};font-size:13px">{escape(note)}</p>')
        parts += [f'<p style="margin:0 0 8px">{escape(l)}</p>' for l in lines[3:]]

    current = None
    for item in brief.news:
        story = item["story"]
        if story.region != current:
            current = story.region
            parts.append(_h2(brief.region_labels[current]))
        parts.append(
            f'<p style="margin:0 0 10px"><a href="{escape(story.link, quote=True)}" '
            f'style="color:{INK};font-weight:600;text-decoration:none">'
            f'{escape(item["headline"])}</a><br>{escape(item["summary"])} '
            f'<span style="color:{MUTED};font-size:13px">{escape(story.source)}</span></p>')

    footer = (f"Made for {' and '.join(adults())} by {brand()}. Reply to this email to add a "
              "TODO, shopping item or event, mark a project step done, or say what to change.")
    if brief.money:
        footer += (f" Money: {brief.money.tx_count} Lunch Money transactions over 130 days, in "
                   f"{primary_code()} at Lunch Money's daily rate; budget {second_code()} lines at "
                   f"{brief.money.fx_rate_plan} {primary_code()} per {second_code()}.")
    if brief.problems:
        footer += " Problems this morning: " + "; ".join(brief.problems) + "."
    if brief.not_set_up:
        footer += " Not connected yet: " + ", ".join(brief.not_set_up) + "."
    for line in brief.updates:
        footer += " " + line
    link = (f' <a href="{escape(brief.portal_url, quote=True)}" style="color:{MUTED}">Open the portal</a>.'
            if brief.portal_url else "")
    parts.append(f'<p style="margin-top:32px;color:{MUTED};font-size:12px;'
                 f'border-top:1px solid {RULE};padding-top:10px">{escape(footer)}{link}</p></div>')
    parts.append(EMAIL_TAIL)
    return "\n".join(parts)


def to_text(brief: Brief) -> str:
    lines = [f"Good morning. {brief.day:%A} {brief.day.day} {brief.day:%B %Y}"]
    if brief.portal_url:
        lines.append(f"Open {brand()}: {brief.portal_url}")
    lines.append("")

    if brief.weather:
        lines += [brief.weather, ""]
    if brief.headsup:
        lines.append("HEADS UP")
        lines += [f"  - {h}" for h in brief.headsup]
        lines.append("")
    if brief.todos:
        lines.append("TO DO")
        lines += [f"  - {_todo_text(t, brief.day)}" for t in brief.todos]
        lines.append("")

    if brief.agenda:
        lines.append("TODAY")
        holiday = _holiday_line(brief.agenda, brief.day)
        if holiday:
            lines.append(holiday)
        for name, events in _by_person(brief, brief.agenda.today):
            lines.append(f"  {name}")
            for e in events:
                title = e.short_title if name != FAMILY else e.title
                lines.append(f"    {e.time_label():<12} {title}{_outside(e, html=False)}"
                             + (f" ({e.location})" if e.location else ""))
            if not events:
                lines.append("    Nothing on the calendar")
        if brief.prep:
            lines += ["", "GET READY FOR"]
            for p in brief.prep:
                lines.append(f"  {p['event'].title}{_outside(p['event'], html=False)} "
                             f"({p['event'].start:%a %d %b}): {p['note']}")
        week, holidays = _week(brief), _upcoming_holidays(brief)
        if week or holidays:
            lines += ["", "THIS WEEK"]
            if holidays:
                lines.append("  " + _holiday_summary(holidays))
            lines += [f"  {e.start:%a} {e.start.day:<2} {e.time_label():<12} "
                      + (f"{e.people[0]}: {e.short_title}" if len(e.people) == 1 else e.title)
                      + _outside(e, html=False) for e in week]

    if brief.next_up:
        lines += ["", "NEXT UP"]
        for who, items, more in next_up_groups(brief.next_up):
            lines.append(f"  {who}")
            lines += [f"    - {i}" for i in items]
            if more:
                lines.append(f"    and {more} more")
        lines += [f"  {l}" for l in countdown_lines(brief.next_up)]
        if brief.practice:
            lines.append(f"  {brief.practice}")
    if brief.phrase:
        sp = brief.phrase
        lines += ["", brief.phrase_label.upper(), f"  {sp['phrase']}: {sp['translation']} ({sp['why']})"]

    if brief.money:
        lines += ["", "MONEY"] + [f"  {l}" for l in brief.networth.splitlines()] \
            + [f"  {l}" for l in money_lines(brief.money)]
        if one_off_note(brief.money):
            lines.append(f"  {one_off_note(brief.money)}")

    current = None
    for item in brief.news:
        story = item["story"]
        if story.region != current:
            current = story.region
            lines += ["", brief.region_labels[current].upper()]
        lines.append(f"  {item['headline']}")
        lines.append(f"    {item['summary']} ({story.source})")
        lines.append(f"    {story.link}")

    if brief.problems:
        lines += ["", "Problems this morning: " + "; ".join(brief.problems) + "."]
    if brief.not_set_up:
        lines += ["", "Not connected yet: " + ", ".join(brief.not_set_up) + "."]
    if brief.updates:
        lines += [""] + brief.updates
    return "\n".join(lines) + "\n"
