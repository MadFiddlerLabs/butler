"""Checks `butler doctor` makes from what the Mac already records: the log, and when the Mac last
restarted. Each one returns problems as (what happened, the fix) in plain words. Read-only: nothing
here changes a file, and nothing it reports names a sender or a secret."""

import os
import re
import subprocess
from datetime import datetime

from . import system

DKIM = "DKIM signature missing or failed"
# Gmail refusing the app password: SMTP says the first, IMAP the second.
GMAIL_LOGIN = ("Username and Password not accepted", "AUTHENTICATIONFAILED")
# Claude refusing the butler's key, as llm.log_failure logs it.
CLAUDE_KEY = "Claude didn't accept your butler's API key"
LOGIN_WAIT_MINUTES = 15      # longer than this between a restart and the first login is worth saying


def log_problems(lines: list[str]) -> list[tuple[str, str]]:
    problems = []
    dkim = sum(DKIM in line for line in lines)
    if dkim:
        problems.append((f"Butler ignored {dkim} recent repl{'y' if dkim == 1 else 'ies'} because the email "
                         "signature (DKIM) was missing or failed.",
                         "Reply from a personal address on the trusted list (Gmail, iCloud or Outlook), not a "
                         "work, alias or forwarded one, then send the reply again"))
    if any(marker in line for line in lines for marker in GMAIL_LOGIN):
        problems.append(("Gmail refused your butler's app password.",
                         "Changing the Gmail password signs out every app password. Make a new app password "
                         "and store it again: " + _store("butler-gmail", "Your butler's Gmail app password")))
    if any(CLAUDE_KEY in line for line in lines):
        problems.append(("Claude refused your butler's API key, so the brief went out without Claude's help.",
                         "The key may have a typo, or it was deleted in the Claude Console. Make a new key there "
                         "and store it: " + _store("butler-anthropic", "Your Claude API key")))
    return problems


def _store(service: str, label: str) -> str:
    """How the student stores a key or password again on this computer: the Mac's dialog, or
    `butler secret` typed hidden in a Terminal window on Windows."""
    if system.NAME == "windows":
        return f"type butler secret {service} in a new Terminal window"
    return f'scripts/store-secret.sh {service} "{label}"'


def boot_time(sysctl: str) -> datetime | None:
    """`sysctl -n kern.boottime` ("{ sec = 1791875552, usec = 0 } Tue Oct 13 09:12:32 2026") as a
    local time."""
    m = re.search(r"sec = (\d+)", sysctl)
    return datetime.fromtimestamp(int(m.group(1))) if m else None


def first_login(last: str, boot: datetime, now: datetime) -> datetime | None:
    """The first console login between `boot` and `now`, from `last -t console <user>` (newest
    first, "sam  console  Tue Oct 13 09:13  still logged in"). `last` leaves out the year: it's
    the boot's, or the next one for a January login after a December restart. Older sessions fall
    outside the window and are left out."""
    since = boot.replace(second=0, microsecond=0)
    logins = []
    for m in re.finditer(r"\bconsole\s+\w{3} (\w{3})\s+(\d{1,2}) (\d\d):(\d\d)", last):
        year = boot.year + (boot.month == 12 and m.group(1) == "Jan")
        try:
            at = datetime.strptime(f"{year} {m.group(1)} {m.group(2)} {m.group(3)}:{m.group(4)}", "%Y %b %d %H:%M")
        except ValueError:
            continue
        if since <= at <= now:
            logins.append(at)
    return min(logins) if logins else None


def restart_problems(boot: datetime | None, login: datetime | None) -> list[tuple[str, str]]:
    """Butler's schedule runs only while someone is logged in, so a Mac that restarted and sat at
    the login screen sent nothing until then. While it's waiting there, nothing of Butler runs,
    this check included: it can only tell afterwards."""
    if not boot or not login or (login - boot).total_seconds() < LOGIN_WAIT_MINUTES * 60:
        return []
    until = f"{login:%H:%M}" if login.date() == boot.date() else f"{login:%a %d %b at %H:%M}"
    return [(f"Your Mac restarted on {boot:%a %d %b at %H:%M} and waited at the login screen until {until}. "
             "Butler only runs while you're logged in, so anything due in between waited.",
             "After a restart, log back in. Butler picks up where it left off")]


def restart_check(user: str, now: datetime | None = None) -> list[tuple[str, str]]:
    """The live check: when this Mac booted and when `user` first logged in after it."""
    try:
        sysctl = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=5)
        last = subprocess.run(["last", "-t", "console", user], capture_output=True, text=True, timeout=5,
                              env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return []
    boot = boot_time(sysctl.stdout)
    return restart_problems(boot, first_login(last.stdout, boot, now or datetime.now()) if boot else None)
