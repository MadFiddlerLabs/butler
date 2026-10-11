"""Writes config/settings.yaml one answer at a time, for the `butler` command and the first-run
setup (course Module 0, "Your first two minutes"). It writes what `/butler-setup install` writes
(Module 2): the butler's name, the adults, the town and its weather. Everything later steps fill stays empty, so the brief lists it
as "not connected yet" instead of showing the example household's details."""

import json
import os
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
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
    s.pop("weather", None)
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


def find_town(query: str) -> Place | None:
    """Looks a town up with Open-Meteo's free geocoding (no key). "Lisbon, Portugal" picks the
    Lisbon in that country."""
    town, _, country = (part.strip() for part in query.partition(","))
    params = urllib.parse.urlencode({"name": town, "count": 10, "language": "en", "format": "json"})
    with urllib.request.urlopen(f"{GEOCODE_URL}?{params}", timeout=10) as r:
        results = json.load(r).get("results") or []
    if country:
        wanted = country.lower()
        results = [x for x in results
                   if wanted in (x.get("country", "").lower(), x.get("country_code", "").lower())] or results
    if not results:
        return None
    x = results[0]
    return Place(x["name"], x.get("country", ""), x.get("country_code", ""),
                 x["latitude"], x["longitude"], x.get("timezone") or "UTC")


def set_town(s: dict, place: Place) -> None:
    s["timezone"] = place.timezone
    s["household"]["place"] = place.name
    s["weather"] = {"place": place.name, "latitude": round(place.latitude, 2),
                    "longitude": round(place.longitude, 2),
                    "unit": "fahrenheit" if place.country_code in FAHRENHEIT_COUNTRIES else "celsius"}


def has_secret(service: str) -> bool:
    """Whether Keychain holds this entry, without reading its value."""
    return subprocess.run(["security", "find-generic-password", "-s", service],
                          capture_output=True).returncode == 0


def first_brief(root: Path = ROOT) -> Path | None:
    briefs = sorted((root / "out" / "preview").glob("brief-*.html"))
    return briefs[-1] if briefs else None
