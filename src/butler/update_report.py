"""After an update, Butler tells buildyourbutler.com how it went: the version it updated from, the
version it updated to, and the outcome. That's how the owner learns a release breaks installs, and
holds it with the kill switch (docs/UPDATES.md) before it reaches everyone else.

- What goes: `{from, to, outcome, reason?}` to `POST /api/updates/outcome` (docs/ACCOUNTS.md).
  Nothing else: no install id, no session, no email, nothing from the household. It's anonymous,
  so a Butler that never signed in reports too.
- `update_report: false` in settings.yaml turns it off. It's on when the setting is absent.
- Best effort: a short timeout, and errors are logged without detail. An update never fails
  because of it.
- Offline, the report waits in `data/updates/outcomes.json` (the same fields and when it happened)
  and the tick sends it, at most every 30 minutes, for up to three days. Like data/progress.json.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import account

log = logging.getLogger("butler")

# Each outcome and the reason codes it may carry: fixed words, never the problem's own text.
REASONS: dict[str, tuple[str, ...]] = {
    "updated": (),
    # Installer stopped halfway, or the new code's checks failed; the kept package went back in.
    "rolled_back": ("install", "check"),
    # Refused before installing (checksum or size, signature), or the way back failed too.
    "failed": ("checksum", "signature", "rollback"),
}
# A release's version, as the website's manifest names it. A test build (v0.0.0-d78f91e) never reports.
VERSION = re.compile(r"v\d{1,4}\.\d{1,4}\.\d{1,4}")
TIMEOUT = 5
MAX_AGE = timedelta(days=3)
RETRY_AFTER = timedelta(minutes=30)
MAX_QUEUED = 10
# A refusal that retrying can't fix: the report is wrong, or the server has no such call.
REFUSED = {400, 404, 405, 413}


def enabled(settings: dict | None) -> bool | None:
    """The household's choice; None when the settings couldn't be read."""
    if not isinstance(settings, dict):
        return None
    return settings.get("update_report", True) is not False


def body(previous, new, outcome: str, reason: str | None = None) -> dict | None:
    """The report, or None when it isn't one Butler sends: a version outside the release format,
    or an outcome and reason that don't go together."""
    if not all(isinstance(v, str) and VERSION.fullmatch(v) for v in (previous, new)):
        return None
    if outcome not in REASONS or (reason is not None and reason not in REASONS[outcome]):
        return None
    return {"from": previous, "to": new, "outcome": outcome, **({"reason": reason} if reason else {})}


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def load(path: Path) -> dict:
    state = {"queued": [], "last_try": None}
    try:
        state.update(json.loads(path.read_text()))
    except (OSError, ValueError):
        pass
    if not isinstance(state["queued"], list):
        state["queued"] = []
    return state


def _save(path: Path, state: dict) -> None:
    if not state["queued"]:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1) + "\n")
    tmp.replace(path)


def _send(state: dict, now: datetime, force: bool) -> None:
    """Sends what waits, oldest first, and stops at the first failure. Drops what's too old."""
    def fresh(item) -> bool:
        at = _parse(item.get("at")) if isinstance(item, dict) else None
        return at is not None and now - at <= MAX_AGE
    state["queued"] = [item for item in state["queued"] if fresh(item)]
    last = _parse(state.get("last_try"))
    if not state["queued"] or (not force and last and now - last < RETRY_AFTER):
        return
    while state["queued"]:
        item = state["queued"][0]
        try:
            account.send_update_outcome(item["from"], item["to"], item["outcome"], item.get("reason"),
                                        timeout=TIMEOUT)
        except account.AccountError as e:
            if e.status in REFUSED:
                log.info("update report: refused (%s)", e.status)
                state["queued"].pop(0)
                continue
            log.info("update report: not sent; it waits for the next try")
            state["last_try"] = _iso(now)
            return
        state["queued"].pop(0)
        log.info("update report: sent")
    state["last_try"] = None


def record(settings: dict | None, path: Path, previous, new, outcome: str, reason: str | None = None,
           now: datetime | None = None) -> None:
    """One update's outcome: queued, then sent at once when the settings allow it. When they
    couldn't be read, it waits for the tick, which reads them. Never raises."""
    try:
        allowed = enabled(settings)
        report = body(previous, new, outcome, reason)
        if allowed is False or report is None:
            return
        now = now or datetime.now(timezone.utc)
        state = load(path)
        state["queued"] = (state["queued"] + [{**report, "at": _iso(now)}])[-MAX_QUEUED:]
        if allowed:
            _send(state, now, force=True)
        _save(path, state)
    except Exception as e:  # noqa: BLE001 - a report never stops an update
        log.info("update report: not recorded (%s)", type(e).__name__)


def flush(settings: dict | None, path: Path, now: datetime | None = None) -> None:
    """The tick: sends what waits, or deletes it when the household turned reports off. Never raises."""
    try:
        if not path.exists():
            return
        allowed = enabled(settings)
        if allowed is False:
            path.unlink(missing_ok=True)
            return
        if allowed is None:
            return
        state = load(path)
        _send(state, now or datetime.now(timezone.utc), force=False)
        _save(path, state)
    except Exception as e:  # noqa: BLE001
        log.info("update report: flush failed (%s)", type(e).__name__)
