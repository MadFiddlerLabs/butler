"""Course progress that marks itself. When a `/butler-setup` step's check passes on this Mac, the
course module it belongs to is reported to the student's Build Your Butler account, so the
website and the weekly lesson email know where they are.

- `course_progress.json` maps each step to its module's lesson ids. The website's engine contract
  check reads it, so the ids can't drift from the course.
- A step counts only when its check passes: the module's win, read from the Butler folder,
  Keychain (whether an entry exists, never its value) and launchd. Starting a step reports nothing.
- What waits to be sent lives in `data/progress.json`: lesson ids and times, nothing else. Offline
  or signed out, it stays queued, and the next tick or `butler` command sends it.
- Nothing here raises or blocks the student. Progress is a nicety, not a dependency.
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import yaml

from . import account
from .config import ROOT, plain_output

MAP_FILE = Path(__file__).with_name("course_progress.json")
STATE_FILE = Path("data") / "progress.json"
# After a failed send, the five-minute tick waits this long before trying again. A `butler`
# command always tries at once.
RETRY_AFTER = timedelta(minutes=30)
# A refusal that retrying can't fix: the server doesn't know the lesson, or the request is wrong.
REFUSED = {400, 404, 413}

log = logging.getLogger("butler")


def course_map() -> dict[str, dict]:
    """{step: {"module": n, "url": its page, "lessons": [ids]}} in course order."""
    return json.loads(MAP_FILE.read_text())["steps"]


def course_modules() -> dict[int, dict]:
    """{n: {"slug", "title"}} for every module, as the course names them."""
    return {int(n): m for n, m in json.loads(MAP_FILE.read_text())["modules"].items()}


def module_url(n: int) -> str:
    """A module's page on the course website, from the same data the contract check compares."""
    site = json.loads(MAP_FILE.read_text())["site"]
    return f"{site}/course/{course_modules()[n]['slug']}/"


# ---------- what the checks can see ----------

@dataclass
class Folder:
    """The Butler folder, plus the two things outside it a check may ask about."""
    root: Path
    has_secret: Callable[[str], bool]       # does Keychain hold this entry? (never its value)
    scheduled: Callable[[], bool]           # is the five-minute launchd job loaded?

    def settings(self) -> dict:
        try:
            return yaml.safe_load((self.root / "config" / "settings.yaml").read_text()) or {}
        except (OSError, yaml.YAMLError):
            return {}

    def example_news(self):
        try:
            return yaml.safe_load((self.root / "config" / "settings.example.yaml").read_text())["news"]["regions"]
        except (OSError, yaml.YAMLError, KeyError, TypeError):
            return None

    def has(self, pattern: str) -> bool:
        return any(self.root.glob(pattern))


def _has_secret(service: str) -> bool:
    try:
        return subprocess.run(["security", "find-generic-password", "-s", service],
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _scheduled() -> bool:
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split("\t")[-1] == "com.buildyourbutler.butler" for line in out.splitlines())


def mac_folder(root: Path | None = None) -> Folder:
    return Folder(root or ROOT, _has_secret, _scheduled)


# ---------- the checks: each is its module's win ----------

def _install(f: Folder) -> bool:
    """Module 2: the household, complete. Named, the adults, the town, their own news (the first
    run never touches news, so this can't pass on Module 0 alone), and a brief."""
    s = f.settings()
    regions = s.get("news", {}).get("regions")
    return (s.get("name") not in (None, "", "Butler") and bool(s.get("household", {}).get("adults"))
            and bool(s.get("weather")) and bool(regions) and regions != f.example_news()
            and f.has("out/preview/brief-*.html"))


def _calendar(f: Folder) -> bool:
    """Module 3: the helper is there and the household picked calendars for the brief."""
    return (f.root / "bin" / "butler-ek").exists() and bool(f.settings().get("calendar", {}).get("include"))


def _brain(f: Folder) -> bool:
    """Module 4: a Claude API key in Keychain, and a few sentences about the household."""
    about = f.settings().get("household", {}).get("about") or ""
    return bool(str(about).strip()) and f.has_secret("butler-anthropic")


def _inbox(f: Folder) -> bool:
    """Module 5: the butler's Gmail in Keychain, its address and recipients set, the schedule
    loaded, and a real brief built for sending (sent briefs live in out/, previews in out/preview/)."""
    email = f.settings().get("email", {})
    return (bool(email.get("sender")) and bool(email.get("recipients")) and f.has("out/brief-*.html")
            and f.has_secret("butler-gmail") and f.scheduled())


def _replies(f: Folder) -> bool:
    """Module 6: the butler has acted on at least one reply."""
    path = f.root / "data" / "processed_replies.txt"
    return path.exists() and path.stat().st_size > 0


def _money(f: Folder) -> bool:
    """Module 7: Lunch Money's token in Keychain, the category map and the budget workbook."""
    return ((f.root / "config" / "category_map.yaml").exists() and (f.root / "budget" / "budget.xlsx").exists()
            and f.has_secret("butler-lunchmoney"))


def _projects(f: Folder) -> bool:
    """Module 8: one of the household's own projects (the reference file doesn't count)."""
    return any(".local." not in p.name for p in (f.root / "projects").glob("*.yaml"))


def _letters(f: Folder) -> bool:
    """Module 9: a weekly letter and a monthly look-back, built (previewed or sent)."""
    weekly = f.has("out/weekly-*.html") or f.has("out/preview/weekly-*.html")
    return weekly and f.has("out/monthly-*.html")


def _phone(f: Folder) -> bool:
    """Module 11: the portal has its tailnet address."""
    return bool(f.settings().get("portal", {}).get("url"))


CHECKS: dict[str, Callable[[Folder], bool]] = {
    "install": _install, "calendar": _calendar, "brain": _brain, "inbox": _inbox, "replies": _replies,
    "money": _money, "projects": _projects, "letters": _letters, "phone": _phone,
}


def passed(folder: Folder, skip: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """The steps whose check passes now, in course order, leaving out `skip` (steps already
    passed: no need to ask Keychain again). A check that can't run hasn't passed."""
    out = []
    for step, check in CHECKS.items():
        if step in skip:
            continue
        try:
            if check(folder):
                out.append(step)
        except Exception:
            log.info("progress: the %s check couldn't run", step)
    return out


# ---------- the queue ----------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(root: Path | None = None) -> dict:
    path = (root or ROOT) / STATE_FILE
    state = {"steps": {}, "queued": {}, "reported": {}, "last_try": None, "last_error": None}
    try:
        state.update(json.loads(path.read_text()))
    except (OSError, ValueError):
        pass
    return state


def save(state: dict, root: Path | None = None) -> None:
    path = (root or ROOT) / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1) + "\n")
    tmp.replace(path)


def queue(state: dict, lesson: str, status: str, at: str) -> None:
    """Adds a lesson to send. Done wins over started, and nothing already sent goes again."""
    for have in (state["reported"].get(lesson), state["queued"].get(lesson)):
        if have and (have["status"] == "done" or have["status"] == status):
            return
    state["queued"][lesson] = {"status": status, "at": at}


def detect(state: dict, folder: Folder, now: str) -> list[str]:
    """Queues every lesson of each step whose check passes for the first time. Returns those steps."""
    steps = course_map()
    new = passed(folder, skip=set(state["steps"]))
    for step in new:
        state["steps"][step] = now
        for lesson in steps[step]["lessons"]:
            queue(state, lesson, "done", now)
    return new


def _session() -> str | None:
    try:
        return account.session()
    except Exception:
        return None


def flush(state: dict, token: str | None, now: str, force: bool = False) -> str | None:
    """Sends what's queued, oldest first, and stops at the first failure. Returns None when all of
    it went, else "signed out" or "offline"."""
    if not state["queued"]:
        return None
    if not token:
        return "signed out"
    if not force and state.get("last_error") and state.get("last_try"):
        last = datetime.fromisoformat(state["last_try"].replace("Z", "+00:00"))
        if datetime.fromisoformat(now.replace("Z", "+00:00")) - last < RETRY_AFTER:
            return state["last_error"]
    state["last_try"] = now
    for lesson, item in list(state["queued"].items()):
        try:
            account.send_progress(token, lesson, item["status"], item["at"])
        except account.AccountError as e:
            if e.status in REFUSED:
                log.warning("progress: the account refused %s (%s)", lesson, e)
                del state["queued"][lesson]
                continue
            state["last_error"] = "signed out" if e.status == 401 else "offline"
            return state["last_error"]
        del state["queued"][lesson]
        state["reported"][lesson] = {**item, "sent_at": now}
    state["last_error"] = None
    return None


def sync(root: Path | None = None, folder: Folder | None = None, force: bool = False) -> tuple[dict, str | None]:
    """Checks every step, queues what newly passed and sends what's waiting. Never raises.
    Returns the state and the problem that kept something from being sent, if any."""
    root = root or ROOT
    try:
        state = load(root)
        now = _now()
        for step in detect(state, folder or mac_folder(root), now):
            log.info("progress: /butler-setup %s passed its check", step)
        # Keychain is read only when there's something to send.
        problem = flush(state, _session() if state["queued"] else None, now, force)
        save(state, root)
        return state, problem
    except Exception as e:
        log.info("progress: sync failed (%s)", type(e).__name__)
        return load(root), "offline"


def report(lesson: str, status: str = "done", root: Path | None = None) -> None:
    """A lesson the `butler` command finished (Module 0's setup.* ids). Sent now, or queued."""
    root = root or ROOT
    try:
        state = load(root)
        now = _now()
        queue(state, lesson, status, now)
        flush(state, _session(), now, force=True)
        save(state, root)
    except Exception as e:
        log.info("progress: report failed (%s)", type(e).__name__)


# ---------- `butler progress` ----------

def _day(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%-d %B")


def lines(state: dict, problem: str | None) -> list[str]:
    """What `butler progress` prints: each step, whether it's done, and whether the course knows."""
    plain = plain_output()   # words only for a screen reader: the marks are for the eye

    def mark(symbol: str) -> str:
        return "  " if plain else f"  {symbol} "
    out = []
    for step, entry in course_map().items():
        where = f"Module {entry['module']}{', ' if plain else ' · '}/butler-setup {step}"
        if step not in state["steps"]:
            out.append(f"{mark('·')}{where}: not done yet")
        elif any(lesson in state["queued"] for lesson in entry["lessons"]):
            out.append(f"{mark('…')}{where}: done, waiting to send")
        else:
            sent = [state["reported"][lesson]["sent_at"] for lesson in entry["lessons"] if lesson in state["reported"]]
            out.append(f"{mark('✓')}{where}: " + (f"sent {_day(max(sent))}" if sent else "done"))
    mapped = {lesson for entry in course_map().values() for lesson in entry["lessons"]}
    other = [lesson for lesson in state["reported"] if lesson not in mapped]
    if other:
        out += ["", "  Also sent: " + ", ".join(other)]
    waiting = [lesson for lesson in state["queued"] if lesson not in mapped]
    if waiting:
        out += ["  Also waiting: " + ", ".join(waiting)]
    if state["queued"]:
        out.append("")
        out.append("You're not signed in, so these wait on this Mac. To sign in, type: butler setup"
                   if problem == "signed out" else
                   "I couldn't reach buildyourbutler.com, so these wait on this Mac. I'll try again in a few minutes.")
    return out
