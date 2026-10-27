"""Google Calendar and Google Tasks, for a household on Windows (no Apple Calendar there) or one
that keeps its calendar in Google. It answers butler-ek's nine commands with the helper's exact
JSON, so every caller works unchanged; calendar_source.py picks which one runs. Like the
helper, it can add and complete, never edit or delete.

    PYTHONPATH=src .venv/bin/python -m butler.gcal connect     # sign in with Google, once
    PYTHONPATH=src .venv/bin/python -m butler.gcal status
    PYTHONPATH=src .venv/bin/python -m butler.gcal calendars   # any helper command, same JSON

The refresh token lives in Keychain (`butler-google`). The access token stays in memory. Event
titles, notes and organisers are outsiders' text: passed through as data, as the helper does.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import time as _clock
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

from . import config, system
from .calendar_source import Failed
from .config import NotSetUp

KEYCHAIN_GOOGLE = "butler-google"
CLIENT_FILE = config.ROOT / "config" / "google-oauth.json"   # git-ignored; the installer writes it
SCOPES = ("https://www.googleapis.com/auth/calendar.readonly",
          "https://www.googleapis.com/auth/calendar.events",
          "https://www.googleapis.com/auth/tasks")
# How to sign in, as the brief and the log say it: Windows has no .venv/bin, and run.py sets the path.
CONNECT = (r".venv\Scripts\python.exe run.py gcal connect" if system.NAME == "windows"
           else "PYTHONPATH=src .venv/bin/python -m butler.gcal connect")
NO_CLIENT = "this install has no Google client ID yet (docs/SETUP.md, \"Google Calendar and Tasks\")"
NOT_CONNECTED = f"Google Calendar isn't connected yet (connect it with: {CONNECT})"
RECONNECT = f"reconnect Google: it no longer accepts Butler's sign-in (run: {CONNECT})"
SOURCE = "Google"
MAX_PAGES = 50

# The only writes Butler makes, as in the helper: sign-in, a new event, a new task list, a new
# task, and a task marked done. _allowed() refuses everything else before it leaves the Mac.
WRITES = (("POST", re.compile(r"/token$")),
          ("POST", re.compile(r"/calendar/v3/calendars/[^/]+/events$")),
          ("POST", re.compile(r"/tasks/v1/users/@me/lists$")),
          ("POST", re.compile(r"/tasks/v1/lists/[^/]+/tasks$")),
          ("PATCH", re.compile(r"/tasks/v1/lists/[^/]+/tasks/[^/]+$")))
COMPLETE = {"status": "completed"}

_access: dict = {}   # {"token", "until"}: this process only, never written down


@dataclass
class Urls:
    auth: str
    token: str
    api: str


def _urls() -> Urls:
    """Google's addresses. Tests point BUTLER_GOOGLE_API at a fake Google on this machine; only a
    loopback address is taken, so nothing can steer the refresh token anywhere else."""
    base = os.environ.get("BUTLER_GOOGLE_API", "").rstrip("/")
    if base.startswith("http://") and urllib.parse.urlparse(base).hostname in ("127.0.0.1", "localhost"):
        return Urls(f"{base}/auth", f"{base}/token", base)
    return Urls("https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token",
                "https://www.googleapis.com")


def _client() -> tuple[str, str]:
    """This install's Google client: from the environment, else config/google-oauth.json (Google's
    downloaded file, or plain client_id and client_secret)."""
    cid, secret = os.environ.get("BUTLER_GOOGLE_CLIENT_ID"), os.environ.get("BUTLER_GOOGLE_CLIENT_SECRET")
    if not (cid and secret) and CLIENT_FILE.exists():
        try:
            data = json.loads(CLIENT_FILE.read_text())
        except ValueError:
            raise NotSetUp(f"{NO_CLIENT}: config/google-oauth.json isn't valid JSON") from None
        data = data.get("installed", data) if isinstance(data, dict) else {}
        cid, secret = data.get("client_id"), data.get("client_secret")
    if not (cid and secret):
        raise NotSetUp(NO_CLIENT)
    return cid, secret


def _allowed(method: str, url: str, body) -> None:
    path = urllib.parse.urlparse(url).path
    if method == "GET":
        return
    if method == "PATCH" and body != COMPLETE:
        raise Failed(f"gcal: refusing to change {path}: Butler only adds and completes")
    if not any(m == method and p.search(path) for m, p in WRITES):
        raise Failed(f"gcal: refusing {method} {path}: Butler only adds and completes")


def _http(method: str, url: str, timeout: float, token: str | None = None, form: dict | None = None,
          body: dict | None = None) -> dict:
    _allowed(method, url, body)
    headers = {"Accept": "application/json", "User-Agent": "butler-gcal/1.0"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw.strip() else {}


def _error(e: urllib.error.HTTPError) -> tuple[str, str]:
    """Google's error code and message from a failed call. Neither ever carries a token."""
    try:
        body = json.loads(e.read() or b"{}")
    except ValueError:
        body = {}
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("status") or err.get("code") or ""), str(err.get("message") or "")[:200]
    return str(err or ""), str(body.get("error_description") or e.reason or "")[:200]


def _remember(tokens: dict) -> None:
    _access.update(token=tokens["access_token"],
                   until=_clock.monotonic() + float(tokens.get("expires_in") or 3600))


def forget() -> None:
    """Drops the access token held in memory."""
    _access.clear()


class Google:
    """Calls to Google's APIs with a fresh access token, refreshed when it runs out."""

    def __init__(self, timeout: float = 60):
        self.timeout = timeout

    def _token(self, fresh: bool = False) -> str:
        if not fresh and _access.get("token") and _access["until"] > _clock.monotonic() + 60:
            return _access["token"]
        cid, secret = _client()
        refresh = config.keychain(KEYCHAIN_GOOGLE)
        if not refresh:
            raise NotSetUp(NOT_CONNECTED)
        form = {"client_id": cid, "client_secret": secret, "refresh_token": refresh, "grant_type": "refresh_token"}
        try:
            got = _http("POST", _urls().token, self.timeout, form=form)
        except urllib.error.HTTPError as e:
            code, message = _error(e)
            forget()
            if code == "invalid_grant":
                raise NotSetUp(RECONNECT) from None
            raise Failed(f"gcal: Google refused the sign-in ({code or e.code}: {message})") from None
        except OSError as e:
            raise Failed(f"gcal: couldn't reach Google ({e})") from None
        _remember(got)
        return _access["token"]

    def call(self, method: str, path: str, query: dict | None = None, body: dict | None = None,
             missing_ok: bool = False) -> dict | None:
        url = _urls().api + path + ("?" + urllib.parse.urlencode(query) if query else "")
        for attempt in (1, 2):
            try:
                return _http(method, url, self.timeout, token=self._token(fresh=attempt == 2), body=body)
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 1:
                    continue      # the access token ran out early: refresh it once
                if e.code == 404 and missing_ok:
                    return None
                code, message = _error(e)
                hint = " (reconnect Google and allow Calendar and Tasks)" if e.code == 403 else ""
                raise Failed(f"gcal: Google answered {e.code} ({message or code}){hint}") from None
            except OSError as e:
                raise Failed(f"gcal: couldn't reach Google ({e})") from None
        raise Failed("gcal: Google refused the sign-in twice")    # pragma: no cover (the loop returns)

    def pages(self, path: str, query: dict) -> list[dict]:
        items, page = [], None
        for _ in range(MAX_PAGES):
            data = self.call("GET", path, {**query, **({"pageToken": page} if page else {})})
            items += data.get("items", [])
            page = data.get("nextPageToken")
            if not page:
                break
        return items


# ---------- dates ----------

def _zone() -> ZoneInfo | timezone:
    """The household's time zone: all-day events and due dates fall on its days, as on the Mac."""
    try:
        return config.tz(config.load_settings())
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc


def _parse(text: str) -> datetime:
    """An ISO time with its offset, or a bare day at midnight where the household lives."""
    try:
        when = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        raise Failed(f"gcal: cannot parse date '{text}'") from None
    return when if when.tzinfo else when.replace(tzinfo=_zone())


def _day(text: str) -> date:
    return _parse(text).astimezone(_zone()).date()


def _iso(when: datetime) -> str:
    """The helper's format: UTC, to the second."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _when(part: dict) -> datetime:
    if part.get("dateTime"):
        return datetime.fromisoformat(part["dateTime"])
    return datetime.combine(date.fromisoformat(part["date"]), time.min, _zone())


# ---------- the nine commands ----------

def _option(args: list[str], name: str) -> str | None:
    flag = f"--{name}"
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None


def _need(args: list[str], *names: str) -> list[str]:
    values = [_option(args, n) for n in names]
    if any(v is None for v in values):
        raise Failed(f"gcal: needs {', '.join('--' + n for n in names)}")
    return values


def _q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _calendar_list(g: Google) -> list[dict]:
    return g.pages("/calendar/v3/users/me/calendarList", {"maxResults": "250"})


def _calendar_info(c: dict) -> dict:
    return {"title": c.get("summaryOverride") or c.get("summary", ""), "source": SOURCE, "id": c["id"],
            "writable": c.get("accessRole") in ("owner", "writer")}


def _task_lists(g: Google) -> list[dict]:
    return g.pages("/tasks/v1/users/@me/lists", {"maxResults": "100"})


def _list_info(t: dict) -> dict:
    return {"title": t.get("title", ""), "source": SOURCE, "id": t["id"], "writable": True}


def _task_list(g: Google, name: str) -> dict:
    found = next((t for t in _task_lists(g) if t.get("title") == name), None)
    if not found:
        raise Failed(f"gcal: no Google Tasks list named '{name}'")
    return found


def _organizer(e: dict) -> dict | None:
    """Who sent the invite, in the helper's shape. Google names an organiser on every event, the
    household's own too, while the helper gives none for an event nobody was invited to: so an
    event of the calendar's own with no guests has none here either. `current_user` is Google's
    `self`: the organiser is this account's calendar. Anyone else stays visible, so Butler marks
    a stranger's invite."""
    o = e.get("organizer") or {}
    if not o or (o.get("self") and not e.get("attendees")):
        return None
    return {"email": o.get("email", ""), "current_user": bool(o.get("self"))}


def _event_row(e: dict, calendar: str) -> dict:
    start, end = _when(e["start"]), _when(e["end"])
    original = e.get("originalStartTime")
    return {
        "id": e.get("recurringEventId") or e["id"],
        "event_id": e["id"],
        "occurrence": _iso(_when(original) if original else start),
        "declined": any(a.get("self") and a.get("responseStatus") == "declined" for a in e.get("attendees", [])),
        "title": e.get("summary", ""),
        "start": _iso(start),
        "end": _iso(end),
        "all_day": "date" in e["start"] and not e["start"].get("dateTime"),
        "recurring": bool(e.get("recurringEventId") or e.get("recurrence")),
        "location": e.get("location", ""),
        "notes": e.get("description", "")[:600],
        "calendar": calendar,
        "source": SOURCE,
        "organizer": _organizer(e),
    }


def calendars(g: Google, args: list[str]):
    return [_calendar_info(c) for c in _calendar_list(g)]


def events(g: Google, args: list[str]):
    start, end = _need(args, "start", "end")
    lo, hi = _parse(start), _parse(end)
    rows = []
    for c in _calendar_list(g):
        title = _calendar_info(c)["title"]
        query = {"timeMin": lo.isoformat(), "timeMax": hi.isoformat(), "singleEvents": "true",
                 "orderBy": "startTime", "maxResults": "2500"}
        for e in g.pages(f"/calendar/v3/calendars/{_q(c['id'])}/events", query):
            # Working-location entries ("Office", "Home") aren't events; Apple's Calendar has none.
            if e.get("status") == "cancelled" or e.get("eventType") == "workingLocation":
                continue
            rows.append(_event_row(e, title))
    return sorted(rows, key=lambda r: (r["start"], r["calendar"], r["title"]))


def reminder_lists(g: Google, args: list[str]):
    return [_list_info(t) for t in _task_lists(g)]


def reminders(g: Google, args: list[str]):
    (name,) = _need(args, "list")
    found = _task_list(g, name)
    rows = []
    query = {"showCompleted": "false", "showHidden": "false", "maxResults": "100"}
    for t in g.pages(f"/tasks/v1/lists/{_q(found['id'])}/tasks", query):
        if t.get("status") == "completed" or t.get("deleted"):
            continue
        row = {"id": t["id"], "title": t.get("title", ""), "notes": t.get("notes", "")}
        if t.get("due"):
            row["due"] = t["due"][:10]     # Google keeps a due day as midnight UTC
        rows.append(row)
    return rows


def ensure_list(g: Google, args: list[str]):
    (name,) = _need(args, "list")
    found = next((t for t in _task_lists(g) if t.get("title") == name), None)
    return _list_info(found or g.call("POST", "/tasks/v1/users/@me/lists", body={"title": name}))


def add_reminder(g: Google, args: list[str]):
    name, title = _need(args, "list", "title")
    found = _task_list(g, name)
    body = {"title": title}
    if _option(args, "notes") is not None:
        body["notes"] = _option(args, "notes")
    if _option(args, "due") is not None:
        body["due"] = f"{_day(_option(args, 'due')).isoformat()}T00:00:00.000Z"
    made = g.call("POST", f"/tasks/v1/lists/{_q(found['id'])}/tasks", body=body)
    return {"id": made["id"], "list": name, "title": title}


def complete_reminder(g: Google, args: list[str]):
    """Google needs the task's list as well as its id; the helper's contract gives only the id, so
    each list is asked in turn."""
    (task_id,) = _need(args, "id")
    if task_id:
        for t in _task_lists(g):
            path = f"/tasks/v1/lists/{_q(t['id'])}/tasks/{_q(task_id)}"
            if g.call("GET", path, missing_ok=True) is not None:
                done = g.call("PATCH", path, body=dict(COMPLETE))
                return {"completed": task_id, "title": done.get("title", "")}
    raise Failed("gcal: no reminder with that id")


def completed(g: Google, args: list[str]):
    name, start, end = _need(args, "list", "start", "end")
    lo, hi = _parse(start), _parse(end)
    found = _task_list(g, name)
    query = {"showCompleted": "true", "showHidden": "true", "completedMin": lo.isoformat(),
             "completedMax": hi.isoformat(), "maxResults": "100"}
    rows = []
    for t in g.pages(f"/tasks/v1/lists/{_q(found['id'])}/tasks", query):
        if t.get("status") != "completed" or not t.get("completed"):
            continue
        when = datetime.fromisoformat(t["completed"])
        if lo <= when < hi:
            rows.append({"title": t.get("title", ""), "completed": _iso(when)})
    return rows


def add_event(g: Google, args: list[str]):
    name = _option(args, "calendar") or ""
    cal = next((c for c in _calendar_list(g) if _calendar_info(c)["title"] == name and _calendar_info(c)["writable"]),
               None)
    if not cal:
        raise Failed(f"gcal: no writable calendar named '{name}'")
    title, start, end = _need(args, "title", "start", "end")
    if _option(args, "all-day") == "1":
        first, last = _day(start), _day(end)
        # Google's all-day end is the day after, as the helper's midnight-to-midnight is.
        when = {"start": {"date": first.isoformat()},
                "end": {"date": max(last, first + timedelta(days=1)).isoformat()}}
    else:
        when = {"start": {"dateTime": _parse(start).isoformat()}, "end": {"dateTime": _parse(end).isoformat()}}
    body = {"summary": title, **when}
    for flag, field in (("location", "location"), ("notes", "description")):
        if _option(args, flag):
            body[field] = _option(args, flag)
    made = g.call("POST", f"/calendar/v3/calendars/{_q(cal['id'])}/events", body=body)
    return {"event_id": made.get("id", ""), "calendar": name, "title": made.get("summary", title)}


COMMANDS = {"calendars": calendars, "events": events, "reminder-lists": reminder_lists, "reminders": reminders,
            "ensure-list": ensure_list, "add-reminder": add_reminder, "complete-reminder": complete_reminder,
            "completed": completed, "add-event": add_event}


def run(args: list[str], timeout: float = 60) -> str:
    """One helper command against Google; the JSON text butler-ek would print."""
    command = args[0] if args else ""
    if command not in COMMANDS:
        raise Failed(f"gcal: unknown command '{command}'")
    out = COMMANDS[command](Google(timeout), args[1:])
    return json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False)


# ---------- signing in ----------

def connect(open_browser=webbrowser.open, wait: float = 300, timeout: float = 30) -> set[str]:
    """Google's sign-in for desktop apps: the browser comes back to a one-off listener on
    127.0.0.1, which closes as soon as it has the answer. PKCE and the `state` check mean only
    this sign-in, started here, can finish it. Keeps the refresh token in Keychain and returns
    any scope Google didn't grant."""
    cid, secret = _client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    answer: dict[str, str] = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass            # the address carries the one-time code

        def _say(self, code: int, text: str) -> None:
            data = text.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got_state = q.get("state", [""])[0]
            if not (got_state and secrets.compare_digest(got_state, state)) or not (q.get("code") or q.get("error")):
                self._say(400, "This isn't the sign-in Butler is waiting for. Go back to the Terminal.")
                return
            answer.update(code=q.get("code", [""])[0], error=q.get("error", [""])[0])
            self._say(200, "Butler is connected to Google. You can close this tab." if answer["code"] else
                      "Google sign-in didn't finish. Go back to the Terminal.")

    server = HTTPServer(("127.0.0.1", 0), Callback)
    try:
        redirect = f"http://127.0.0.1:{server.server_address[1]}/"
        url = _urls().auth + "?" + urllib.parse.urlencode({
            "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": " ".join(SCOPES),
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
            "access_type": "offline", "prompt": "consent"})
        print("Opening Google's sign-in page. If your browser doesn't open, paste this address into it:")
        print(url, flush=True)
        open_browser(url)
        server.timeout = 1
        deadline = _clock.monotonic() + wait
        while not answer and _clock.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not answer:
        raise Failed("gcal: Google sign-in timed out. Run connect again.")
    if not answer["code"]:
        raise Failed(f"gcal: Google sign-in didn't finish ({answer['error']})")
    form = {"client_id": cid, "client_secret": secret, "code": answer["code"], "code_verifier": verifier,
            "redirect_uri": redirect, "grant_type": "authorization_code"}
    try:
        tokens = _http("POST", _urls().token, timeout, form=form)
    except urllib.error.HTTPError as e:
        code, message = _error(e)
        raise Failed(f"gcal: Google refused the sign-in ({code or e.code}: {message})") from None
    if not tokens.get("refresh_token"):
        raise Failed("gcal: Google didn't give Butler a lasting sign-in. Run connect again.")
    config.store_secret(KEYCHAIN_GOOGLE, tokens["refresh_token"])
    _remember(tokens)
    return set(SCOPES) - set(str(tokens.get("scope", "")).split())


def status(timeout: float = 30) -> str:
    """Whether Google is ready, in words. Never shows a token."""
    try:
        _client()
    except NotSetUp as e:
        return f"Not ready: {e}."
    if not config.keychain(KEYCHAIN_GOOGLE):
        return f"Google isn't connected yet. Connect it with: {CONNECT}"
    try:
        g = Google(timeout)
        found = len(_calendar_list(g)), len(_task_lists(g))
    except (NotSetUp, Failed) as e:
        return f"Google is connected, but: {e}"
    return f"Google is connected: {found[0]} calendars and {found[1]} task lists."


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else ""
    try:
        if command == "connect":
            missing = connect()
            print("Google is connected. Butler can read your calendars and add to them and to your tasks.")
            if missing:
                print("Google didn't allow: " + ", ".join(sorted(missing)) + ". Run connect again and tick every box.")
            return 0
        if command == "status":
            print(status())
            return 0
        if command in COMMANDS:
            print(run(argv))
            return 0
    except (NotSetUp, Failed) as e:
        print(e, file=sys.stderr)
        return 1
    print(f"usage: gcal <connect|status|{'|'.join(sorted(COMMANDS))}> [options]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
