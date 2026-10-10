import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[2]
BUTLER_EK = ROOT / "bin" / "butler-ek"

# Keychain service names. Add them with:
#   security add-generic-password -s <service> -a butler -w
KEYCHAIN_GMAIL = "butler-gmail"
KEYCHAIN_ANTHROPIC = "butler-anthropic"


def config_path(name: str) -> Path:
    """config/<name>.yaml, or config/<name>.example.yaml when you haven't made your own yet,
    so a fresh clone runs on the examples."""
    real = ROOT / "config" / f"{name}.yaml"
    return real if real.exists() else ROOT / "config" / f"{name}.example.yaml"


def load_settings() -> dict:
    with open(config_path("settings")) as f:
        return yaml.safe_load(f)


def tz(settings: dict) -> ZoneInfo:
    return ZoneInfo(settings["timezone"])


def brand(settings: dict | None = None) -> str:
    """The household's name for the assistant, used in subjects and page titles."""
    return (settings or load_settings()).get("name", "Butler")


def people(settings: dict | None = None) -> list[str]:
    """The adults the brief is for, in order."""
    return [p["name"] for p in (settings or load_settings()).get("household", {}).get("adults", [])]


def name_for_email(address: str, settings: dict | None = None) -> str | None:
    """The adult a sender address belongs to, from household.adults[].emails."""
    address = address.lower()
    for p in (settings or load_settings()).get("household", {}).get("adults", []):
        if address in [e.lower() for e in p.get("emails", [])]:
            return p["name"]
    return None


def keychain(service: str) -> str | None:
    """Returns a secret from the login Keychain, or None if it isn't there."""
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-w"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def portal_url(settings: dict | None = None) -> str:
    return (settings or load_settings()).get("portal", {}).get("url", "")
