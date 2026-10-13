import functools
import os
import re
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[2]
BUTLER_EK = ROOT / "bin" / "butler-ek"

# Keychain service names. Add them with scripts/store-secret.sh, or by hand with this, which asks
# for the value (twice) instead of taking it on the command line:
#   security add-generic-password -U -s <service> -a butler -w
KEYCHAIN_GMAIL = "butler-gmail"
KEYCHAIN_ANTHROPIC = "butler-anthropic"


class NotSetUp(RuntimeError):
    """A part the household hasn't connected yet. Normal while someone works through setup,
    so the brief notes it quietly instead of reporting a problem."""


def butler_ek() -> str:
    """The Calendar and Reminders helper, once `/butler-setup calendar` has built it."""
    if not BUTLER_EK.exists():
        raise NotSetUp("the Calendar and Reminders helper isn't built yet (/butler-setup calendar)")
    return str(BUTLER_EK)


@functools.cache
def plain_output() -> bool:
    """Terminal text for a screen reader: words only, no tick marks, dots or other symbols that
    VoiceOver reads aloud or skips. On with BUTLER_PLAIN=1, when CLAUDE_AX_SCREEN_READER is set
    (the course's accessibility page has VoiceOver users set it for Claude Code), or while
    VoiceOver runs. BUTLER_PLAIN=0 turns it off. scripts/lib/plain.sh decides the same way."""
    flag = os.environ.get("BUTLER_PLAIN", "").strip().lower()
    if flag in ("1", "yes", "true"):
        return True
    if flag in ("0", "no", "false"):
        return False
    if os.environ.get("CLAUDE_AX_SCREEN_READER", "").strip().lower() not in ("", "0", "no", "false"):
        return True
    try:
        return subprocess.run(["pgrep", "-x", "VoiceOver"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def config_path(name: str) -> Path:
    """config/<name>.yaml, or config/<name>.example.yaml when you haven't made your own yet,
    so a fresh clone runs on the examples."""
    real = ROOT / "config" / f"{name}.yaml"
    return real if real.exists() else ROOT / "config" / f"{name}.example.yaml"


def on_example() -> bool:
    """True until the household has its own settings.yaml: the app is showing the Johnsons."""
    return not (ROOT / "config" / "settings.yaml").exists()


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


def store_secret(service: str, secret: str) -> None:
    """Saves a secret in the login Keychain. Never on a command line, where any account on this
    Mac can read it in the process list: `security -i` reads its command from stdin, and the
    secret travels in it hex-encoded (-X), so no character in it can break the command.
    scripts/lib/keychain.sh does the same for the shell scripts."""
    if not re.fullmatch(r"butler-[a-z]+", service):
        raise ValueError(f"not a Butler Keychain service: {service}")
    command = f"add-generic-password -U -s {service} -a butler -X {secret.encode().hex()}\n"
    subprocess.run(["security", "-i"], input=command, text=True, check=True, capture_output=True)


def portal_url(settings: dict | None = None) -> str:
    return (settings or load_settings()).get("portal", {}).get("url", "")
