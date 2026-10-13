"""A fake buildyourbutler.com account API (the contract in docs/ACCOUNTS.md), plus Open-Meteo's
geocoding, on a local port. Tests start it, point BUTLER_API and BUTLER_GEOCODE_URL at it, and
read back what the setup sent. tests/test_account_contract.py runs the doc's table through it,
so it can't drift from the contract.

    api = FakeAccountAPI(auto_verify=True); api.start()
    ... BUTLER_API=api.url + "/api" ...
    api.stop()
"""

import hashlib
import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOWNS = {
    "lisbon": [{"name": "Lisbon", "country": "Portugal", "country_code": "PT", "latitude": 38.71667,
                "longitude": -9.13333, "timezone": "Europe/Lisbon"},
               {"name": "Lisbon", "country": "United States", "country_code": "US", "latitude": 44.03,
                "longitude": -70.1, "timezone": "America/New_York"}],
    "springfield": [{"name": "Springfield", "country": "United States", "country_code": "US",
                     "latitude": 39.8, "longitude": -89.64, "timezone": "America/Chicago"}],
}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class FakeAccountAPI:
    LESSON = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
    VERSION = re.compile(r"^v(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
    # POST /api/updates/outcome: each outcome and the reasons it may carry, as the real API takes them.
    OUTCOMES = {"updated": (), "rolled_back": ("install", "check"), "failed": ("checksum", "signature", "rollback")}

    def __init__(self, auto_verify: bool = False, expire: bool = False, unavailable: bool = False,
                 known_lessons: set[str] | None = None):
        self.auto_verify = auto_verify
        self.known_lessons = known_lessons   # when set, other lesson ids get 400 bad_lesson, like the real API
        self.progress_down = False           # /progress answers 503, like a server having a bad minute
        self.outcomes_down = False           # /updates/outcome answers 503
        self.outcomes: list[dict] = []           # every update outcome reported, as sent
        self.outcome_auth: list[str | None] = [] # the Authorization header of each (it must be None)
        self.unavailable = unavailable   # sign-in down: /auth/start answers 503, like the real API without Resend
        self.expire = expire
        self.requests: dict[str, dict] = {}      # request_id -> {email, butler_name, poll_hash, token, status}
        self.sessions: dict[str, str] = {}       # sha256(session) -> email
        self.users: dict[str, dict] = {}         # email -> {butler_name}
        self.progress: list[dict] = []           # every progress call, in order
        self.prefs: list[dict] = []
        self.calls: list[tuple[str, str, dict]] = []
        self.links: list[str] = []               # magic-link tokens "emailed"
        self.issued: list[str] = []              # every poll secret and session token handed out
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> "FakeAccountAPI":
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def click(self, token: str | None = None) -> None:
        """What the student's click on the emailed link does."""
        self._verify(token or self.links[-1])

    def _verify(self, token: str) -> dict | None:
        for r in self.requests.values():
            if r["link_hash"] == _hash(token) and r["status"] == "pending":
                r["status"] = "verified"
                return r
        return None

    def me(self, email: str) -> dict:
        """GET /api/me: one entry per lesson, and a done lesson stays done."""
        lessons: dict[str, dict] = {}
        for p in self.progress:
            if p["email"] == email and lessons.get(p["lesson"], {}).get("status") != "done":
                lessons[p["lesson"]] = {"lesson": p["lesson"], "status": p["status"], "at": p["at"]}
        nudges = [p["nudges"] for p in self.prefs if p["email"] == email]
        return {"email": email, "butler_name": self.users.get(email, {}).get("butler_name", ""),
                "nudges": nudges[-1] if nudges else False, "progress": list(lessons.values())}

    def valid_outcome(self, body: dict) -> bool:
        """The real API's rules: exactly these fields, release versions, a newer `to`, a known
        outcome, and a reason only from that outcome's list."""
        if not isinstance(body, dict) or not set(body) <= {"from", "to", "outcome", "reason"}:
            return False
        versions = [self.VERSION.match(v) if isinstance(v, str) else None for v in (body.get("from"), body.get("to"))]
        if not all(versions) or tuple(map(int, versions[1].groups())) <= tuple(map(int, versions[0].groups())):
            return False
        reasons = self.OUTCOMES.get(body.get("outcome"))
        return reasons is not None and ("reason" not in body or body["reason"] in reasons)

    def _handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _bearer(self) -> str:
                return self.headers.get("Authorization", "").removeprefix("Bearer ").strip()

            def _session(self) -> str | None:
                token = self._bearer()
                return api.sessions.get(_hash(token)) if token else None

            def do_GET(self):
                url = urlparse(self.path)
                api.calls.append(("GET", url.path, {}))
                if url.path == "/geocode":
                    name = parse_qs(url.query).get("name", [""])[0].lower()
                    return self._send(200, {"results": TOWNS[name]} if name in TOWNS else {})
                if url.path == "/api/me":
                    email = self._session()
                    if not email:
                        return self._send(401, {"error": "not signed in"})
                    return self._send(200, api.me(email))
                self._send(404, {"error": "not found"})

            def do_POST(self):
                url = urlparse(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    return self._send(400, {"error": "bad json"})
                api.calls.append(("POST", url.path, body))
                if url.path == "/api/auth/start":
                    if api.unavailable:
                        return self._send(503, {"error": "email_unavailable"})
                    if "@" not in body.get("email", ""):
                        return self._send(400, {"error": "that email doesn't look right"})
                    heard = body.get("heard")      # optional; the real API refuses non-text or over 40 characters
                    if heard is not None and (not isinstance(heard, str) or len(heard) > 40):
                        return self._send(400, {"error": '"heard" must be a short answer.', "code": "bad_heard"})
                    request_id, poll_secret, link = (secrets.token_urlsafe(16), secrets.token_urlsafe(32),
                                                     secrets.token_urlsafe(32))
                    api.requests[request_id] = {"email": body["email"], "butler_name": body.get("butler_name", ""),
                                                "poll_hash": _hash(poll_secret), "link_hash": _hash(link),
                                                "status": "pending", "issued": False,
                                                "client": str(body.get("client", "")),
                                                "terms_version": body.get("terms_version"),
                                                "terms_accepted_at": body.get("terms_accepted_at"),
                                                "heard": heard}
                    api.links.append(link)
                    api.issued.append(poll_secret)
                    if api.auto_verify:
                        api._verify(link)
                    return self._send(200, {"request_id": request_id, "poll_secret": poll_secret,
                                            "expires_in": 900})
                if url.path == "/api/auth/verify":
                    request = api._verify(body.get("token", ""))
                    if request:
                        client = "web" if request["client"].startswith("web") else "setup"
                        return self._send(200, {"ok": True, "client": client})
                    return self._send(400, {"error": "this link expired or was already used"})
                if url.path == "/api/auth/poll":
                    r = api.requests.get(body.get("request_id", ""))
                    if not r or r["poll_hash"] != _hash(body.get("poll_secret", "")):
                        return self._send(404, {"error": "unknown request"})
                    if api.expire:
                        return self._send(200, {"status": "expired"})
                    if r["status"] == "verified" and not r["issued"]:
                        r["issued"] = True
                        session = secrets.token_urlsafe(32)
                        api.issued.append(session)
                        api.sessions[_hash(session)] = r["email"]
                        if r["butler_name"]:
                            api.users.setdefault(r["email"], {})["butler_name"] = r["butler_name"]
                        return self._send(200, {"status": "verified", "session_token": session})
                    return self._send(200, {"status": "verified" if r["issued"] else r["status"]})
                if url.path == "/api/progress":
                    email = self._session()
                    if not email:
                        return self._send(401, {"error": "not signed in"})
                    if api.progress_down:
                        return self._send(503, {"error": "server", "code": "server"})
                    lesson, status = body.get("lesson"), body.get("status")
                    if not isinstance(lesson, str) or not api.LESSON.match(lesson) or (
                            api.known_lessons is not None and lesson not in api.known_lessons):
                        return self._send(400, {"error": "Unknown lesson id.", "code": "bad_lesson"})
                    if status not in ("started", "done"):
                        return self._send(400, {"error": "bad status", "code": "bad_status"})
                    api.progress.append({"email": email, "lesson": lesson, "status": status, "at": body.get("at")})
                    return self._send(200, {"ok": True})
                if url.path == "/api/updates/outcome":
                    if api.outcomes_down:
                        return self._send(503, {"error": "server", "code": "server"})
                    if not api.valid_outcome(body):
                        return self._send(400, {"error": "That isn't an update outcome.", "code": "bad_outcome"})
                    api.outcomes.append(body)
                    api.outcome_auth.append(self.headers.get("Authorization"))
                    return self._send(200, {"ok": True})
                if url.path == "/api/prefs":
                    email = self._session()
                    if not email:
                        return self._send(401, {"error": "not signed in"})
                    if not isinstance(body.get("nudges"), bool):
                        return self._send(400, {"error": "nudges must be true or false"})
                    api.prefs.append({"email": email, "nudges": body["nudges"]})
                    return self._send(200, {"ok": True})
                if url.path == "/api/auth/logout":
                    if not self._session():
                        return self._send(401, {"error": "not signed in"})
                    del api.sessions[_hash(self._bearer())]
                    return self._send(200, {"ok": True})
                if url.path == "/api/account/delete":
                    email = self._session()
                    if not email:
                        return self._send(401, {"error": "not signed in"})
                    if body.get("confirm") is not True:
                        return self._send(400, {"error": "confirm must be true"})
                    api.sessions = {h: e for h, e in api.sessions.items() if e != email}
                    api.users.pop(email, None)
                    api.progress = [p for p in api.progress if p["email"] != email]
                    api.prefs = [p for p in api.prefs if p["email"] != email]
                    return self._send(200, {"ok": True})
                self._send(404, {"error": "not found"})

        return Handler
