"""Loaded at start-up by every Python process of the Butler a persona test installed (a .pth file
the harness puts in Butler's venv), including the ones launchd or Task Scheduler start. It points
the outside services a student signs up for at the run's fakes:

- Gmail: smtplib.SMTP_SSL writes each message to ~/persona-fakes/mail, and imaplib.IMAP4_SSL
  serves the persona's replies from ~/persona-fakes/imap. Both take only the address and app
  password the persona made on the fake Google screens.
- Lunch Money: its API answers from tests/fakes/fixtures.py, for the token the persona was shown.
- Claude: Butler's own calls go to the harness's relay, which checks the key the persona stored
  and forwards to the real API, within the run's budget.
- Google: Calendar and Tasks go to the harness's FakeGoogle; a browser that would open goes to
  the persona as an event.
- Updates: the manifest says this version is the newest, so no real release replaces the test build.

The news, the weather and the account API stay as they are. Nothing else is touched.
"""
import email.utils
import imaplib
import io
import json
import os
import smtplib
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
# At the end, so nothing here can stand in for a module Butler itself imports.
sys.path.append(str(HERE))
sys.path.append(str(HERE.parent / "fakes"))
import events  # noqa: E402

FAKES = events.FAKES


def _gmail_ok(user: str, password: str) -> bool:
    gmail = events.config().get("gmail", {})
    return bool(gmail.get("address")) and user.strip().lower() == gmail["address"].lower() \
        and "".join(password.split()) in gmail.get("app_passwords", [])


def install() -> None:
    cfg = events.config()
    if not cfg:
        return
    import harness
    fakes = str(HERE.parent / "fakes")   # harness.py puts its folder first; here it goes last
    sys.path[:] = [p for p in sys.path if p != fakes] + [fakes]
    # harness.py guards the network for the offline tests; here the news and weather are real.
    socket.create_connection = harness._real_create_connection
    socket.socket.connect = harness._real_connect

    class GmailSMTP(harness.FakeSMTP):
        def login(self, user, password):
            if not _gmail_ok(user, password):
                raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted. For more "
                                                           b"information, go to https://support.google.com/mail/?p=BadCredentials")
            self.user = user

        def send_message(self, msg, *args, **kwargs):
            plain, html = msg.get_body(("plain",)), msg.get_body(("html",))
            to = [a for _, a in email.utils.getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Bcc", []))]
            (FAKES / "mail").mkdir(parents=True, exist_ok=True)
            (FAKES / "mail" / f"{time.time_ns()}.json").write_text(json.dumps({
                "from": msg["From"], "to": to, "subject": msg["Subject"], "message_id": msg["Message-ID"] or "",
                "in_reply_to": msg["In-Reply-To"] or "", "text": plain.get_content() if plain else "",
                "html": html.get_content() if html else ""}, ensure_ascii=False), encoding="utf-8")
            return {}

    class GmailIMAP(harness.FakeIMAP):
        folder = FAKES / "imap"
        seen_file = FAKES / "imap-seen.json"

        def login(self, user, password):
            if not _gmail_ok(user, password):
                raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
            return "OK", [b"logged in"]

    real_urlopen = urllib.request.urlopen

    def urlopen(req, *args, **kwargs):
        url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc != harness.LUNCH_MONEY:
            return real_urlopen(req, *args, **kwargs)
        auth = req.headers.get("Authorization", "") if isinstance(req, urllib.request.Request) else ""
        if auth.removeprefix("Bearer ") not in events.config().get("lunchmoney_tokens", []):
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(b'{"message": "Access token does not exist."}'))
        query = dict(urllib.parse.parse_qsl(parsed.query))
        return io.BytesIO(json.dumps(harness._lunch_money(parsed.path.removeprefix("/v2/"), query, url)).encode())

    def open_browser(url, *args, **kwargs):
        events.ask("browser", {"url": url}, timeout=0)
        return True

    smtplib.SMTP_SSL = GmailSMTP
    imaplib.IMAP4_SSL = GmailIMAP
    urllib.request.urlopen = urlopen
    webbrowser.open = open_browser
    os.environ["ANTHROPIC_BASE_URL"] = cfg["relay"]
    os.environ["BUTLER_GOOGLE_API"] = cfg["google_api"]
    os.environ["BUTLER_GOOGLE_CLIENT_ID"] = cfg["google_client_id"]
    os.environ["BUTLER_GOOGLE_CLIENT_SECRET"] = cfg["google_client_secret"]
    os.environ["BUTLER_UPDATE_MANIFEST"] = cfg["manifest"]


try:
    install()
except Exception as e:  # noqa: BLE001 - a broken hook must show, but never stop Butler starting
    print(f"(persona test hook failed: {type(e).__name__}: {e})", file=sys.stderr)
