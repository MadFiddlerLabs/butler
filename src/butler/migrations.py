"""Settings migrations, run by the new code after an update (updates.run_post_install).

Each migration has a number, runs once, and is safe to run again: it checks before it changes.
Applied numbers go in data/migrations.json. A migration changes a household's files only to add
what a new version needs, and it appends rather than rewrites, so comments and order survive.

Add one by appending to MIGRATIONS with the next number. Never renumber or remove one.
"""

import json

import yaml

from .config import ROOT

RECORD = ROOT / "data" / "migrations.json"
SETTINGS = ROOT / "config" / "settings.yaml"


def _append_settings(block: str, key: str) -> bool:
    """Appends `block` to settings.yaml unless `key` is already set. True if it changed the file."""
    if not SETTINGS.exists():
        return False
    text = SETTINGS.read_text()
    if key in (yaml.safe_load(text) or {}):
        return False
    SETTINGS.write_text(text.rstrip("\n") + "\n\n" + block.strip("\n") + "\n")
    return True


def m001_update_settings() -> bool:
    """Butler 0.1 adds over-the-air updates: make the household's choice visible in settings."""
    return _append_settings(
        "# Updates: auto installs new versions overnight (a major version still asks first),\n"
        "# notify always asks, off never checks. update_hour is when, in your timezone.\n"
        "updates: auto\n"
        "update_hour: 3\n",
        "updates")


MIGRATIONS = [
    (1, "update settings", m001_update_settings),
]


def applied() -> list[int]:
    try:
        return list(json.loads(RECORD.read_text()).get("applied", []))
    except (OSError, ValueError):
        return []


def run_pending() -> list[int]:
    """Runs every migration not yet recorded, in order, recording each as it finishes. Returns
    the numbers it ran."""
    done = set(applied())
    ran = []
    for number, _name, fn in sorted(MIGRATIONS):
        if number in done:
            continue
        fn()
        done.add(number)
        ran.append(number)
        RECORD.parent.mkdir(parents=True, exist_ok=True)
        RECORD.write_text(json.dumps({"applied": sorted(done)}, indent=2) + "\n")
    return ran
