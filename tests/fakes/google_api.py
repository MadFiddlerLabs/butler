"""A fake Google on a local port: the OAuth endpoints for desktop apps, Calendar API v3 and Tasks
API v1, in the shapes Google's reference gives. It serves the Johnsons' invented week from
fixtures.py, so a household on Google gets the same brief as one on Apple. It records every
call, answers 405 to any method Butler must never use, refuses query parameters the real API
doesn't list, and can expire the access tokens or revoke the refresh token.

    google = FakeGoogle().start()
    home.run("daily", env=google.env())   # BUTLER_GOOGLE_API, the client ID and secret
    google.calls                          # [(method, path, query, body)]
    google.stop()
"""

import base64
import hashlib
import json
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from . import fixtures

CLIENT_ID = "fake-client.apps.googleusercontent.example"
CLIENT_SECRET = "fake-google-client-secret-5t2r"
REFRESH_TOKEN = fixtures.SECRETS["butler-google"]
SCOPES = ("https://www.googleapis.com/auth/calendar.readonly",
          "https://www.googleapis.com/auth/calendar.events",
          "https://www.googleapis.com/auth/tasks")

# The Johnsons' calendars as Google lists them (invented). Alex's has a name Alex gave it
# (summaryOverride); the holiday calendar's id has a '#', as Google's do, and is read-only.
CALENDARS = [
    {"kind": "calendar#calendarListEntry", "id": "family.johnson@group.calendar.example",
     "summary": "Johnson Family", "accessRole": "owner", "timeZone": "Europe/Lisbon"},
    {"kind": "calendar#calendarListEntry", "id": "sam@work.example.com", "summary": "Sam Work",
     "accessRole": "owner", "timeZone": "Europe/Lisbon", "primary": True},
    {"kind": "calendar#calendarListEntry", "id": "alex@example.com", "summary": "alex@example.com",
     "summaryOverride": "Alex", "accessRole": "writer", "timeZone": "Europe/Lisbon"},
    {"kind": "calendar#calendarListEntry", "id": "en.portuguese#holiday@group.v.calendar.google.com",
     "summary": "Holidays in Portugal", "accessRole": "reader", "timeZone": "Europe/Lisbon"},
]
LIST_IDS = {"Bento": "list-bento", "Shopping": "list-shopping"}

# The query parameters Google's reference lists for each read Butler makes.
PARAMS = {"calendarList": {"maxResults", "pageToken"},
          "events": {"timeMin", "timeMax", "singleEvents", "orderBy", "maxResults", "pageToken"},
          "tasklists": {"maxResults", "pageToken"},
          "tasks": {"showCompleted", "showHidden", "completedMin", "completedMax", "maxResults", "pageToken"}}
MAX_RESULTS = {"calendarList": 250, "events": 2500, "tasklists": 1000, "tasks": 100}
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def _title(cal: dict) -> str:
    return cal.get("summaryOverride") or cal["summary"]


def _id(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:20]


def _stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def google_event(row: dict, calendar_id: str) -> dict:
    """A fixtures.events() row as Calendar API v3 returns it with singleEvents=true."""
    start, end = datetime.fromisoformat(row["start"]), datetime.fromisoformat(row["end"])
    if row["all_day"]:
        s, e = {"date": start.date().isoformat()}, {"date": end.date().isoformat()}
    else:
        s = {"dateTime": start.isoformat(), "timeZone": "Europe/Lisbon"}
        e = {"dateTime": end.isoformat(), "timeZone": "Europe/Lisbon"}
    event = {"kind": "calendar#event", "id": _id(row["event_id"]), "status": "confirmed", "eventType": "default",
             "summary": row["title"], "start": s, "end": e,
             # Google names an organiser on every event: here, the calendar itself.
             "organizer": {"email": calendar_id, "self": True}}
    if row["location"]:
        event["location"] = row["location"]
    if row["notes"]:
        event["description"] = row["notes"]
    if row["recurring"]:
        event["recurringEventId"] = _id(row["title"])
        event["originalStartTime"] = s
    if row["organizer"]:
        org = row["organizer"]
        event["organizer"] = {"email": org["email"], **({"self": True} if org["current_user"] else {})}
        event["attendees"] = [{"email": org["email"], "organizer": True, "responseStatus": "accepted"},
                              {"email": "sam@example.com", "self": True, "responseStatus": "needsAction"}]
    return event


class FakeGoogle:
    def __init__(self, page_size: int = 2):
        self.page_size = page_size          # small, so Butler must follow nextPageToken
        self.calls: list[tuple[str, str, dict, dict | None]] = []
        self.refused: list[tuple[str, str]] = []   # DELETE, PUT and the like: never allowed
        self.granted = set(SCOPES)          # what the consent screen grants
        self.deny = False                   # the person presses Cancel on Google's page
        self.revoked = False                # the refresh token stops working: invalid_grant
        self.codes: dict[str, dict] = {}
        self.access: set[str] = set()       # access tokens still valid
        self.lists = [{"kind": "tasks#taskList", "id": LIST_IDS[name], "title": name} for name in fixtures.REMINDERS]
        self.tasks: dict[str, list[dict]] = {
            LIST_IDS[name]: [{"kind": "tasks#task", "id": r["id"], "title": r["title"], "status": "needsAction",
                              **({"notes": r["notes"]} if r["notes"] else {}),
                              **({"due": f"{r['due']}T00:00:00.000Z"} if r.get("due") else {})}
                             for r in rows]
            for name, rows in fixtures.REMINDERS.items()}
        self.added_events: dict[str, list[dict]] = {}
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> "FakeGoogle":
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def env(self) -> dict[str, str]:
        return {"BUTLER_GOOGLE_API": self.url, "BUTLER_GOOGLE_CLIENT_ID": CLIENT_ID,
                "BUTLER_GOOGLE_CLIENT_SECRET": CLIENT_SECRET}

    def expire_tokens(self) -> None:
        """Every access token stops working, as one does an hour after it's issued."""
        self.access.clear()

    def writes(self) -> list[tuple[str, str, dict | None]]:
        return [(m, p, b) for m, p, _, b in self.calls if m != "GET" and p != "/token"]

    # ---------- what the API holds ----------

    def _events(self, cal: dict, lo: datetime, hi: datetime) -> list[dict]:
        rows = [r for r in fixtures.events(lo, hi) if r["calendar"] == _title(cal)]
        out = [google_event(r, cal["id"]) for r in rows]
        if cal["id"] == "sam@work.example.com":
            # Two entries Butler must skip: a working location and a cancelled meeting.
            day = lo.astimezone(fixtures.ZONE).date().isoformat()
            nxt = (lo.astimezone(fixtures.ZONE).date() + timedelta(days=1)).isoformat()
            out += [{"kind": "calendar#event", "id": "wl1", "status": "confirmed", "eventType": "workingLocation",
                     "summary": "Office", "start": {"date": day}, "end": {"date": nxt},
                     "organizer": {"email": cal["id"], "self": True}},
                    {"kind": "calendar#event", "id": "gone1", "status": "cancelled", "summary": "Moved meeting",
                     "start": {"date": day}, "end": {"date": nxt}}]
        for e in self.added_events.get(cal["id"], []):
            start = datetime.fromisoformat(e["start"].get("dateTime") or e["start"]["date"] + "T00:00:00+00:00")
            if lo <= start < hi:
                out.append(e)
        return out

    def _completed(self, list_id: str, query: dict) -> list[dict]:
        done = [t for t in self.tasks[list_id] if t["status"] == "completed"]
        if "completedMin" in query:
            lo = datetime.fromisoformat(query["completedMin"])
            # The stub helper's answer for any window: one done 10 hours into it.
            done = [{"kind": "tasks#task", "id": f"done-{list_id}", "title": "Return the library books",
                     "status": "completed", "completed": _stamp(lo + timedelta(hours=10)), "hidden": True}] + done
            done = [t for t in done if lo <= datetime.fromisoformat(t["completed"])]
        if "completedMax" in query:
            hi = datetime.fromisoformat(query["completedMax"])
            done = [t for t in done if datetime.fromisoformat(t["completed"]) <= hi]
        return done

    def _page(self, items: list, query: dict, kind: str) -> dict:
        offset = int(query.get("pageToken") or 0)
        size = min(int(query.get("maxResults", MAX_RESULTS[kind])), self.page_size)
        out = {"items": items[offset:offset + size]}
        if offset + size < len(items):
            out["nextPageToken"] = str(offset + size)
        return out

    # ---------- the endpoints ----------

    def _token(self, form: dict) -> tuple[int, dict]:
        if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
            return 401, {"error": "invalid_client", "error_description": "The OAuth client was not found."}
        grant = form.get("grant_type")
        if grant == "authorization_code":
            code = self.codes.pop(form.get("code", ""), None)
            verifier = form.get("code_verifier", "")
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if not code or code["challenge"] != challenge or code["redirect_uri"] != form.get("redirect_uri"):
                return 400, {"error": "invalid_grant", "error_description": "Bad Request"}
            access = self._issue()
            return 200, {"access_token": access, "expires_in": 3599, "refresh_token": REFRESH_TOKEN,
                         "scope": " ".join(sorted(self.granted)), "token_type": "Bearer"}
        if grant == "refresh_token":
            if self.revoked or form.get("refresh_token") != REFRESH_TOKEN:
                return 400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
            return 200, {"access_token": self._issue(), "expires_in": 3599,
                         "scope": " ".join(sorted(self.granted)), "token_type": "Bearer"}
        return 400, {"error": "unsupported_grant_type"}

    def _issue(self) -> str:
        token = "fake-access-" + secrets.token_urlsafe(12)
        self.access.add(token)
        return token

    def _authorize(self, query: dict) -> tuple[int, str]:
        """Google's consent page, already answered: a redirect back to Butler's listener."""
        redirect = query.get("redirect_uri", "")
        ok = (query.get("client_id") == CLIENT_ID and query.get("response_type") == "code"
              and query.get("code_challenge_method") == "S256" and query.get("code_challenge")
              and query.get("state") and set(query.get("scope", "").split()) == set(SCOPES)
              and re.fullmatch(r"http://127\.0\.0\.1:\d+/", redirect))
        if not ok:
            return 400, ""
        if self.deny:
            return 302, redirect + "?" + urlencode({"error": "access_denied", "state": query["state"]})
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"challenge": query["code_challenge"], "redirect_uri": redirect}
        return 302, redirect + "?" + urlencode({"code": code, "state": query["state"]})

    def _api(self, method: str, parts: list[str], query: dict, body: dict | None) -> tuple[int, dict]:
        def bad(code: int, message: str) -> tuple[int, dict]:
            return code, {"error": {"code": code, "message": message, "status": "INVALID_ARGUMENT"}}

        def params(kind: str) -> tuple[int, dict] | None:
            unknown = set(query) - PARAMS[kind]
            if unknown:
                return bad(400, f"unknown parameters {sorted(unknown)}")
            if int(query.get("maxResults", 1)) > MAX_RESULTS[kind]:
                return bad(400, "maxResults too large")
            return None

        cals = {c["id"]: c for c in CALENDARS}
        tasklists = {t["id"]: t for t in self.lists}
        if parts == ["calendar", "v3", "users", "me", "calendarList"] and method == "GET":
            return params("calendarList") or (200, self._page(CALENDARS, query, "calendarList"))
        if parts[:3] == ["calendar", "v3", "calendars"] and len(parts) == 5 and parts[4] == "events":
            cal = cals.get(parts[3])
            if not cal:
                return bad(404, "Not Found")
            if method == "GET":
                problem = params("events")
                if problem:
                    return problem
                if not all(RFC3339.match(query.get(k, "")) for k in ("timeMin", "timeMax")):
                    return bad(400, "timeMin and timeMax must be RFC3339 with an offset")
                if query.get("orderBy") == "startTime" and query.get("singleEvents") != "true":
                    return bad(400, "orderBy=startTime needs singleEvents=true")
                events = self._events(cal, datetime.fromisoformat(query["timeMin"]),
                                      datetime.fromisoformat(query["timeMax"]))
                return 200, {**self._page(events, query, "events"), "timeZone": cal["timeZone"]}
            if method == "POST":
                if cal["accessRole"] not in ("owner", "writer"):
                    return bad(403, "You need to have writer access to this calendar.")
                if not body or set(body) - {"summary", "start", "end", "location", "description"} \
                        or not body.get("start") or not body.get("end"):
                    return bad(400, "Bad event")
                if "date" in body["start"] and body["end"].get("date", "") <= body["start"]["date"]:
                    return bad(400, "The specified time range is empty.")
                event = {"kind": "calendar#event", "id": _id(json.dumps(body) + secrets.token_hex(4)),
                         "status": "confirmed", "eventType": "default", **body,
                         "organizer": {"email": cal["id"], "self": True}}
                self.added_events.setdefault(cal["id"], []).append(event)
                return 200, event
        if parts == ["tasks", "v1", "users", "@me", "lists"]:
            if method == "GET":
                return params("tasklists") or (200, self._page(self.lists, query, "tasklists"))
            if method == "POST":
                if not body or set(body) != {"title"}:
                    return bad(400, "Bad task list")
                made = {"kind": "tasks#taskList", "id": f"list-{len(self.lists) + 1}", "title": body["title"]}
                self.lists.append(made)
                self.tasks[made["id"]] = []
                return 200, made
        if parts[:3] == ["tasks", "v1", "lists"] and len(parts) in (5, 6) and parts[4] == "tasks":
            if parts[3] not in tasklists:
                return bad(404, "Not Found")
            items = self.tasks[parts[3]]
            if len(parts) == 5 and method == "GET":
                problem = params("tasks")
                if problem:
                    return problem
                shown = [t for t in items if t["status"] != "completed"]
                if query.get("showCompleted", "true") == "true":
                    done = self._completed(parts[3], query)
                    # A task completed in Google's apps is hidden; showHidden brings it back.
                    shown += done if query.get("showHidden") == "true" else [t for t in done if not t.get("hidden")]
                return 200, self._page(shown, query, "tasks")
            if len(parts) == 5 and method == "POST":
                if not body or not body.get("title") or set(body) - {"title", "notes", "due"}:
                    return bad(400, "Bad task")
                task = {"kind": "tasks#task", "id": f"t{sum(len(v) for v in self.tasks.values()) + 1}",
                        "status": "needsAction", **body}
                if "due" in body:
                    if not RFC3339.match(body["due"]):
                        return bad(400, "Invalid due")
                    task["due"] = body["due"][:10] + "T00:00:00.000Z"    # Google keeps only the day
                items.insert(0, task)
                return 200, task
            task = next((t for t in items if len(parts) == 6 and t["id"] == parts[5]), None)
            if not task:
                return bad(404, "Not Found")
            if method == "GET":
                return 200, task
            if method == "PATCH":
                task.update(body or {})
                if task.get("status") == "completed":
                    task["completed"] = _stamp(datetime.now(timezone.utc))
                    task["hidden"] = True
                return 200, task
        return bad(404, "Not Found")

    def _handler(self):
        google = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=UTF-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _read(self):
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def _handle(self, method: str):
                url = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                raw = self._read()
                if url.path == "/token" and method == "POST":
                    form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
                    google.calls.append((method, url.path, {}, {"grant_type": form.get("grant_type")}))
                    return self._send(*google._token(form))
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    return self._send(400, {"error": {"code": 400, "message": "bad json"}})
                google.calls.append((method, url.path, query, body))
                if url.path == "/auth" and method == "GET":
                    code, location = google._authorize(query)
                    self.send_response(code)
                    if location:
                        self.send_header("Location", location)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if method not in ("GET", "POST", "PATCH"):
                    google.refused.append((method, url.path))
                    return self._send(405, {"error": {"code": 405, "message": "Butler never does this"}})
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Bearer ") or auth.removeprefix("Bearer ") not in google.access:
                    return self._send(401, {"error": {"code": 401, "status": "UNAUTHENTICATED",
                                                      "message": "Request had invalid authentication credentials."}})
                parts = [unquote(p) for p in url.path.strip("/").split("/")]
                self._send(*google._api(method, parts, query, body))

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_PATCH(self):
                self._handle("PATCH")

            def do_PUT(self):
                self._handle("PUT")

            def do_DELETE(self):
                self._handle("DELETE")

        return Handler
