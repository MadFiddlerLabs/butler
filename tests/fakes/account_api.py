"""A fake buildyourbutler.com account API (the contract in docs/ACCOUNTS.md), plus Open-Meteo's
geocoding, on a local port. Tests start it, point BUTLER_API and BUTLER_GEOCODE_URL at it, and
read back what the setup sent.

    api = FakeAccountAPI(auto_verify=True); api.start()
    ... BUTLER_API=api.url + "/api" ...
    api.stop()
"""

import hashlib
import json
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
    def __init__(self, auto_verify: bool = False, expire: bool = False):
        self.auto_verify = auto_verify
        self.expire = expire
        self.requests: dict[str, dict] = {}      # request_id -> {email, butler_name, poll_hash, token, status}
        self.sessions: dict[str, str] = {}       # sha256(session) -> email
        self.progress: list[dict] = []
        self.calls: list[tuple[str, str, dict]] = []
        self.links: list[str] = []               # magic-link tokens "emailed"
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

    def _verify(self, token: str) -> bool:
        for r in self.requests.values():
            if r["link_hash"] == _hash(token) and r["status"] == "pending":
                r["status"] = "verified"
                return True
        return False

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

            def _session(self) -> str | None:
                auth = self.headers.get("Authorization", "")
                token = auth.removeprefix("Bearer ").strip()
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
                    return self._send(200, {"email": email, "butler_name": "", "progress": api.progress})
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
                    if "@" not in body.get("email", ""):
                        return self._send(400, {"error": "that email doesn't look right"})
                    request_id, poll_secret, link = (secrets.token_urlsafe(16), secrets.token_urlsafe(32),
                                                     secrets.token_urlsafe(32))
                    api.requests[request_id] = {"email": body["email"], "butler_name": body.get("butler_name", ""),
                                                "poll_hash": _hash(poll_secret), "link_hash": _hash(link),
                                                "status": "pending", "issued": False,
                                                "terms_version": body.get("terms_version"),
                                                "terms_accepted_at": body.get("terms_accepted_at")}
                    api.links.append(link)
                    if api.auto_verify:
                        api._verify(link)
                    return self._send(200, {"request_id": request_id, "poll_secret": poll_secret,
                                            "expires_in": 900})
                if url.path == "/api/auth/verify":
                    if api._verify(body.get("token", "")):
                        return self._send(200, {"ok": True})
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
                        api.sessions[_hash(session)] = r["email"]
                        return self._send(200, {"status": "verified", "session_token": session})
                    return self._send(200, {"status": "verified" if r["issued"] else r["status"]})
                if url.path == "/api/progress":
                    email = self._session()
                    if not email:
                        return self._send(401, {"error": "not signed in"})
                    api.progress.append({"email": email, "lesson": body.get("lesson"), "status": body.get("status")})
                    return self._send(200, {"ok": True})
                if url.path == "/api/auth/logout":
                    return self._send(200, {"ok": True})
                self._send(404, {"error": "not found"})

        return Handler
