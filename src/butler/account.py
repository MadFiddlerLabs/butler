"""The student's Build Your Butler account: sign-in by magic link and course progress.

Everything here is best effort. Butler works fully offline and without an account. The account
only keeps the student's place in the course. The session lives in Keychain (`butler-account`),
never in a file. The server holds the email, the butler's name and lesson progress, nothing from
the household.
"""

import json
import os
import subprocess
import urllib.error
import urllib.request

from .config import ROOT, keychain

API = os.environ.get("BUTLER_API", "https://buildyourbutler.com/api").rstrip("/")
KEYCHAIN_ACCOUNT = "butler-account"
TERMS_FILE = ROOT / "data" / "terms.json"          # written when the student agrees, in first-run.sh
TIMEOUT = 8


class AccountError(RuntimeError):
    pass


def _call(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            message = json.load(e).get("error") or e.reason
        except ValueError:
            message = e.reason
        finally:
            e.close()
        raise AccountError(str(message)) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise AccountError(f"couldn't reach buildyourbutler.com ({e})") from None


def terms() -> dict:
    """The Terms version the student agreed to and when, recorded locally by the first run. It goes
    with every sign-in, so an agreement made offline still reaches the server."""
    try:
        recorded = json.loads(TERMS_FILE.read_text())
        return {k: recorded[k] for k in ("terms_version", "terms_accepted_at")}
    except (OSError, ValueError, KeyError):
        return {}


def start(email: str, butler_name: str, client: str) -> dict:
    return _call("POST", "/auth/start", {"email": email, "butler_name": butler_name, "client": client, **terms()})


def poll(request_id: str, poll_secret: str) -> dict:
    return _call("POST", "/auth/poll", {"request_id": request_id, "poll_secret": poll_secret})


def session() -> str | None:
    return keychain(KEYCHAIN_ACCOUNT)


def save_session(token: str) -> None:
    subprocess.run(["security", "add-generic-password", "-U", "-s", KEYCHAIN_ACCOUNT, "-a", "butler",
                    "-w", token], check=True, capture_output=True)


def me() -> dict | None:
    token = session()
    if not token:
        return None
    try:
        return _call("GET", "/me", token=token)
    except AccountError:
        return None


def record(lesson: str, status: str = "done") -> bool:
    """Tells the course a lesson moved on. Never raises: progress is a nicety, not a dependency."""
    token = session()
    if not token:
        return False
    try:
        _call("POST", "/progress", {"lesson": lesson, "status": status}, token=token)
        return True
    except AccountError:
        return False
