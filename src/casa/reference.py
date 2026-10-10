"""Useful information (phones, IDs, addresses) and document expiry dates, from
projects/reference.local.yaml (git-ignored; start from examples/reference.local.example.yaml).
Only the portal's Info page shows the numbers; anything that reaches an email or Claude uses
document names and dates only (see expiring())."""

from dataclasses import dataclass
from datetime import date

import yaml

from .config import ROOT

REFERENCE = ROOT / "projects" / "reference.local.yaml"
WARN_DAYS = (90, 30)


def load() -> dict:
    if not REFERENCE.exists():
        return {}
    return yaml.safe_load(REFERENCE.read_text()) or {}


@dataclass
class Expiring:
    holder: str
    document: str
    expires: date
    days: int


def expiring(today: date, within: int = WARN_DAYS[0]) -> list[Expiring]:
    """Documents expiring within `within` days (or already expired). No numbers."""
    out = []
    for d in load().get("documents", []):
        if not d.get("expires"):
            continue
        exp = d["expires"] if isinstance(d["expires"], date) else date.fromisoformat(str(d["expires"]))
        days = (exp - today).days
        if days <= within:
            out.append(Expiring(d["holder"], d["document"], exp, days))
    return sorted(out, key=lambda e: e.days)
