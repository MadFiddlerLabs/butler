"""Heads up: deadlines and document expiries coming soon. Names and dates only, so it's safe
for email (ID numbers stay on the portal's Info page)."""

from dataclasses import dataclass
from datetime import date

import yaml

from . import reference
from .config import config_path

DEADLINES = config_path("deadlines")


@dataclass
class Item:
    when: date
    days: int
    text: str
    who: str = ""


def deadlines(today: date, within: int) -> list[Item]:
    if not DEADLINES.exists():
        return []
    out = []
    for d in yaml.safe_load(DEADLINES.read_text()).get("deadlines", []):
        month, day = map(int, str(d["date"]).split("-"))
        for year in (today.year, today.year + 1):
            if year < d.get("from_year", 0):
                continue
            when = date(year, month, day)
            days = (when - today).days
            if 0 <= days <= within:
                out.append(Item(when, days, f"{d['name']}. {d.get('note', '')}".strip(), d.get("who", "")))
    return out


def documents(today: date) -> list[Item]:
    out = []
    for e in reference.expiring(today, within=reference.WARN_DAYS[0]):
        verb = "expired" if e.days < 0 else "expires"
        out.append(Item(e.expires, e.days, f"{e.holder}'s {e.document} {verb} on {e.expires:%-d %B %Y}. "
                                           "Start the renewal.", e.holder))
    return out


def items(today: date, within: int) -> list[Item]:
    return sorted(deadlines(today, within) + documents(today), key=lambda i: i.days)


def line(i: Item) -> str:
    when = "today" if i.days == 0 else "tomorrow" if i.days == 1 else (
        f"{-i.days} days ago" if i.days < 0 else f"in {i.days} days ({i.when:%a %-d %b})")
    return f"{i.text} ({when})" if i.days < 0 else f"{when[0].upper()}{when[1:]}: {i.text}"
