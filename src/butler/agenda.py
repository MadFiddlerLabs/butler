"""Reads events from the household's calendar: Apple's through the butler-ek helper, or Google's
(calendar_source.py)."""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import calendar_source

# Events the Apps Script copied from another calendar (apps-script/butler-calendar.gs).
COPY_NOTE = re.compile(r"^Copied from \S+@\S+")

# The quiet marker on an invite from outside the household, in the emails.
OUTSIDE_MARK = " (invite from outside)"
# The same, where an event reaches Claude: always inside a marked data block.
OUTSIDE_LABEL = "invite from outside the household"


def is_outside(e) -> bool:
    """Whether an event is an invite from outside the household. Events built elsewhere (the
    screenshots, a test) may not have the field: those are the household's own."""
    return bool(getattr(e, "outside", False))


@dataclass
class Event:
    ref: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    recurring: bool
    location: str
    notes: str
    calendar: str
    people: list[str]
    short_title: str  # title without a leading owner name: "Sam Swimming" -> "Swimming"
    outside: bool = False  # organised by someone outside the household (from_outside)

    def time_label(self) -> str:
        if self.all_day:
            return "All day"
        return f"{self.start:%H:%M}–{self.end:%H:%M}"


@dataclass
class Agenda:
    day: date
    today: list[Event]
    upcoming: list[Event]
    holidays: dict[date, list[str]]


def _assign(title: str, calendar: str, people: list[dict]) -> tuple[list[str], str]:
    """Returns (people the event belongs to, title to show under that person)."""
    named = [
        p for p in people
        if any(re.search(rf"\b{re.escape(a)}\b", title, re.I) for a in p["aliases"])
    ]
    if len(named) == 1:
        for alias in named[0]["aliases"]:
            short = re.sub(rf"^{re.escape(alias)}\b\s*[-–:]?\s*", "", title, flags=re.I)
            # Keep the title whole when what remains still names the same person.
            leftover_alias = any(re.search(rf"\b{re.escape(a)}\b", short, re.I)
                                 for a in named[0]["aliases"])
            if short != title and short and not leftover_alias:
                return [named[0]["name"]], short[:1].upper() + short[1:]
    if named:
        return [p["name"] for p in named], title
    return [p["name"] for p in people if calendar in p.get("calendars", [])][:1], title


def household_addresses(settings: dict) -> set[str]:
    """Every address settings give the household: the adults' own, the trusted senders, the
    recipients and the butler's mailbox."""
    email = settings.get("email") or {}
    found = [e for p in (settings.get("household") or {}).get("adults", []) for e in p.get("emails", [])]
    found += (email.get("trusted_senders") or []) + (email.get("recipients") or []) + [email.get("sender") or ""]
    return {str(a).strip().lower() for a in found if a}


def from_outside(raw: dict, addresses: set[str]) -> bool:
    """True for an invite whose organiser isn't the household: not the person signed in on this
    Mac and not an address in settings. An event with no organiser is one the household made.
    An organiser without an address counts as outside: unknown isn't ours."""
    org = raw.get("organizer")
    if not org or org.get("current_user"):
        return False
    return (org.get("email") or "").strip().lower() not in addresses


def _fetch(start: datetime, end: datetime, settings: dict | None = None) -> list[dict]:
    return json.loads(calendar_source.run(["events", "--start", start.isoformat(), "--end", end.isoformat()],
                                          timeout=60, settings=settings))


def load_agenda(settings: dict, day: date, zone: ZoneInfo) -> Agenda:
    cfg = settings["calendar"]
    day_start = datetime.combine(day, time.min, zone)
    day_end = day_start + timedelta(days=1)
    window_end = day_end + timedelta(days=cfg["lookahead_days"])

    include, holiday_cals = set(cfg["include"]), set(cfg["holidays"])
    ours = household_addresses(settings)
    seen: set[tuple] = set()
    events: list[Event] = []
    holidays: dict[date, list[str]] = {}

    for raw in _fetch(day_start, window_end, settings):
        start = datetime.fromisoformat(raw["start"]).astimezone(zone)
        end = datetime.fromisoformat(raw["end"]).astimezone(zone)
        title = raw["title"].strip()
        if raw["calendar"] in holiday_cals:
            names = holidays.setdefault(start.date(), [])
            if title not in names:
                names.append(title)
            continue
        # Copies would show twice; the original is enough.
        if raw["calendar"] not in include or COPY_NOTE.match(raw["notes"]):
            continue
        # The same meeting often sits on several calendars; show it once.
        key = (title.lower(), start)
        if key in seen:
            continue
        seen.add(key)
        people, short_title = _assign(title, raw["calendar"], settings.get("people", []))
        events.append(Event(
            ref=f"e{len(events) + 1}", title=title, start=start, end=end,
            all_day=raw["all_day"], recurring=raw.get("recurring", False),
            location=raw["location"].strip(), notes=raw["notes"].strip(),
            calendar=raw["calendar"],
            people=people, short_title=short_title, outside=from_outside(raw, ours),
        ))

    events.sort(key=lambda e: (e.start, not e.all_day))
    today = [e for e in events if e.start < day_end and e.end > day_start]
    upcoming = [e for e in events if e.start >= day_end]
    return Agenda(day=day, today=today, upcoming=upcoming, holidays=holidays)
