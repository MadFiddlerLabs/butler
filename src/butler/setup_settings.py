"""Writes config/settings.yaml one answer at a time, for the `butler` command and the first-run
setup (course Module 0, "Your first two minutes"). It writes what `/butler-setup install` writes
(Module 2): the butler's name, the adults, the town and its weather. Everything later steps fill stays empty, so the brief lists it
as "not connected yet" instead of showing the example household's details."""

import json
import os
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import ROOT

SETTINGS = ROOT / "config" / "settings.yaml"
EXAMPLE = ROOT / "config" / "settings.example.yaml"
GEOCODE_URL = os.environ.get("BUTLER_GEOCODE_URL", "https://geocoding-api.open-meteo.com/v1/search")
PLACEHOLDER_NAME = "Butler"
# Person colors from Google Calendar's palette (light, dark), in the order people are added.
COLORS = [("#3f51b5", "#8c9eff"), ("#e67c73", "#f4a39d"), ("#33b679", "#6fd3a0"),
          ("#8e24aa", "#c77ddb"), ("#f4511e", "#ff8a65"), ("#039be5", "#4fc3f7")]
OPTIONAL_FEATURES = ("language_learning", "score_tracker")
FAHRENHEIT_COUNTRIES = {"US", "LR", "MM", "BS", "BZ", "KY", "PW", "FM", "MH"}
HEADER = ("# Written by the `butler` command. Edit freely: docs/CONFIG.md explains every setting.\n"
          "# Never put ID numbers, account numbers or passwords in this file.\n")


def valid_name(name: str) -> str | None:
    """A butler's or person's name as the website allows it: letters, digits, spaces, hyphens and
    apostrophes, at most 24 characters. Returns it tidied, or None."""
    if any(c.isspace() and c != " " for c in name):
        return None
    name = " ".join(name.split())
    if not 1 <= len(name) <= 24 or not any(c.isalnum() for c in name):
        return None
    if not all(c.isalnum() or c in " -'" for c in name):
        return None
    return name


def mac_timezone() -> str:
    """The Mac's own timezone, a safe default until `butler town` sets the household's."""
    try:
        link = os.readlink("/etc/localtime")
        return link.split("zoneinfo/", 1)[1]
    except (OSError, IndexError):
        return "UTC"


def fresh() -> dict:
    """New settings from the example, with the invented Johnsons' details taken out."""
    s = yaml.safe_load(EXAMPLE.read_text())
    s["name"] = PLACEHOLDER_NAME
    s["timezone"] = mac_timezone()
    s["email"].update(sender="", sender_name=PLACEHOLDER_NAME, recipients=[], trusted_senders=[])
    s["household"].update(adults=[], place="", about="")
    s["people"] = []
    s["calendar"].update(include=[], holidays=[], write_to="")
    s["reminders"]["todo_list"] = PLACEHOLDER_NAME
    s["portal"].update(url="", allowed_logins=[], people={})
    s["llm"].pop("monthly_limit", None)    # theirs, from the Console: /butler-setup brain asks
    s.pop("weather", None)
    s.pop("claude_mode", None)              # theirs to choose: `butler claude` asks once
    for key in OPTIONAL_FEATURES:
        s.pop(key, None)
    return s


def load() -> dict:
    """The household's settings, or fresh ones when there are none yet."""
    return yaml.safe_load(SETTINGS.read_text()) if SETTINGS.exists() else fresh()


def save(s: dict) -> None:
    SETTINGS.parent.mkdir(exist_ok=True)
    SETTINGS.write_text(HEADER + yaml.safe_dump(s, sort_keys=False, allow_unicode=True))


def set_name(s: dict, name: str) -> None:
    s["name"] = name
    s["email"]["sender_name"] = name
    s["reminders"]["todo_list"] = name


def set_adults(s: dict, names: list[str]) -> None:
    """The adults the emails are for. Keeps the addresses already known for a name, and the other
    people (children, a nanny) after the adults."""
    known = {a["name"]: a for a in s["household"].get("adults", [])}
    s["household"]["adults"] = [{"name": n, "emails": known.get(n, {}).get("emails", [])} for n in names]
    people = {p["name"]: p for p in s.get("people", [])}
    ordered = [people.get(n) or {"name": n, "aliases": [n], "calendars": []} for n in names]
    ordered += [p for n, p in people.items() if n not in names]
    for i, p in enumerate(ordered):
        if not p.get("color"):
            light, dark = COLORS[i % len(COLORS)]
            p["color"], p["color_dark"] = light, dark
    s["people"] = ordered


@dataclass
class Place:
    name: str
    country: str
    country_code: str
    latitude: float
    longitude: float
    timezone: str
    region: str = ""                                   # Open-Meteo's admin1: a state, province or region
    others: list[str] = field(default_factory=list)    # other towns of the name: "Springfield, Illinois"

    def label(self) -> str:
        """"Springfield, Illinois, United States": enough to tell it from the others."""
        return ", ".join(p for p in (self.name, self.region, self.country) if p)


# Open-Meteo names US states in full; people often type the two letters ("Paris, TX").
US_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california", "co": "colorado",
    "ct": "connecticut", "de": "delaware", "dc": "district of columbia", "fl": "florida", "ga": "georgia",
    "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas",
    "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland", "ma": "massachusetts",
    "mi": "michigan", "mn": "minnesota", "ms": "mississippi", "mo": "missouri", "mt": "montana",
    "ne": "nebraska", "nv": "nevada", "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico",
    "ny": "new york", "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma",
    "or": "oregon", "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina", "sd": "south dakota",
    "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia", "wa": "washington",
    "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming",
}


def _names(x: dict) -> set[str]:
    """What a part after a comma may say to pick this result: its country or country code, its
    state or region (with or without a trailing "Region"), or its county."""
    names = {str(x.get(k) or "").lower() for k in ("country", "country_code", "admin1", "admin2")}
    names |= {n.removesuffix(" region") for n in names}
    return names - {""}


def _matches(x: dict, part: str) -> bool:
    part = part.lower()
    names = _names(x)
    return part in names or US_STATES.get(part, "") in names


def _short(x: dict) -> str:
    return ", ".join(p for p in (x["name"], x.get("admin1") or x.get("country") or "") if p)


def find_town(query: str) -> Place | None:
    """Looks a town up with Open-Meteo's free geocoding (no key). Each part after a comma narrows
    it down: "Springfield, Illinois" and "Paris, TX" by state or region, "Lisbon, Portugal" by
    country. Otherwise the biggest town of that name wins, so the Place names the others too."""
    town, *rest = [part.strip() for part in query.split(",")]
    rest = [part for part in rest if part]
    params = urllib.parse.urlencode({"name": town, "count": 20, "language": "en", "format": "json"})
    with urllib.request.urlopen(f"{GEOCODE_URL}?{params}", timeout=10) as r:
        results = json.load(r).get("results") or []
    # Open-Meteo also returns towns whose other names match ("Springfield" finds Palmyra).
    results = [x for x in results if x.get("name", "").lower() == town.lower()] or results
    if rest:
        results = [x for x in results if all(_matches(x, part) for part in rest)] or results
    if not results:
        return None
    x = results[0]
    others = []
    for o in results[1:]:
        if _short(o) != _short(x) and _short(o) not in others:
            others.append(_short(o))
    return Place(x["name"], x.get("country", ""), x.get("country_code", ""), x["latitude"], x["longitude"],
                 x.get("timezone") or "UTC", x.get("admin1") or "", others[:2])


def set_town(s: dict, place: Place) -> None:
    """The town, its timezone and the weather's place. The rest of the weather block stays, the
    unit too, unless the town is in another country now: then it's that country's usual unit."""
    old = s.get("weather") or {}
    s["timezone"] = place.timezone
    s["household"]["place"] = place.name
    unit = old.get("unit")
    if not unit or old.get("country", place.country_code) != place.country_code:
        unit = "fahrenheit" if place.country_code in FAHRENHEIT_COUNTRIES else "celsius"
    s["weather"] = {**old, "place": place.name, "latitude": round(place.latitude, 2),
                    "longitude": round(place.longitude, 2), "unit": unit, "country": place.country_code}


CLAUDE_MODES = ("ask", "auto")
# Claude Code's names for them (code.claude.com/docs/en/permission-modes): `default`, shown as
# "manual mode on", checks in before each change; `auto` has a safety check review actions instead.
PERMISSION_MODES = {"ask": "default", "auto": "auto"}


def claude_mode(s: dict) -> str | None:
    """How Claude Code works in the Butler folder, "ask" or "auto", or None when not chosen yet."""
    mode = str(s.get("claude_mode") or "").strip().lower()
    return mode if mode in CLAUDE_MODES else None


def done_steps(root: Path = ROOT) -> list[str]:
    """The first run's lessons already done, so scripts/first-run.sh can skip them: "town",
    "adults" and "brief"."""
    try:
        s = yaml.safe_load((root / "config" / "settings.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        s = {}
    done = []
    if s.get("weather"):
        done.append("town")
    if (s.get("household") or {}).get("adults"):
        done.append("adults")
    if first_brief(root) is not None:
        done.append("brief")
    return done


def has_secret(service: str) -> bool:
    """Whether Keychain holds this entry, without reading its value."""
    return subprocess.run(["security", "find-generic-password", "-s", service],
                          capture_output=True).returncode == 0


def first_brief(root: Path = ROOT) -> Path | None:
    briefs = sorted((root / "out" / "preview").glob("brief-*.html"))
    return briefs[-1] if briefs else None


if __name__ == "__main__":
    # scripts/first-run.sh asks which lessons it can skip, before the `butler` command is needed.
    import sys
    if sys.argv[1:] == ["done"]:
        print(" ".join(done_steps()))
