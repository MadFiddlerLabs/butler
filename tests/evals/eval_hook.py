"""Loaded at start-up by every Python process of the Butler a skill eval installed (a .pth file
run_evals.py puts in Butler's venv), including the ones Task Scheduler starts. It points the
services a student signs up for at the run's fakes, so nothing reaches a real account:

- Gmail: smtplib.SMTP_SSL writes each message to <fakes>/mail, and takes only the butler's address
  and the app password the scripted student stored. imaplib.IMAP4_SSL is an empty inbox.
- Claude: Butler's own calls go to run_evals.FakeClaude on this machine. The run's real key is
  taken out of the environment, so no Butler process can use it.
- Google: Calendar and Tasks go to FakeGoogle (tests/fakes/google_api.py). A browser that would open
  goes to <fakes>/browser.jsonl instead; Google's consent page answers by itself, as a student
  who allows everything.
- Updates: the manifest says this version is the newest, so no real release replaces the test build.

<fakes> is ~/butler-eval-fakes, or BUTLER_EVAL_FAKES. Without its config.json, nothing changes.
Standard library only.
"""
import json
import os
import smtplib
import imaplib
import threading
import time
import urllib.request
import webbrowser
from email.utils import getaddresses
from pathlib import Path

FAKES = Path(os.environ.get("BUTLER_EVAL_FAKES") or Path.home() / "butler-eval-fakes")


def config() -> dict:
    try:
        return json.loads((FAKES / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _gmail_ok(user: str, password: str) -> bool:
    gmail = config().get("gmail") or {}
    return bool(gmail.get("address")) and user.strip().lower() == gmail["address"].lower() \
        and "".join(password.split()) == gmail.get("app_password")


class GmailSMTP:
    def __init__(self, host="", port=0, *args, **kwargs):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if not _gmail_ok(user, password):
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted.")
        return 235, b"Accepted"

    def send_message(self, msg, *args, **kwargs):
        plain, html = msg.get_body(("plain",)), msg.get_body(("html",))
        to = [a for _, a in getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Bcc", []))]
        (FAKES / "mail").mkdir(parents=True, exist_ok=True)
        (FAKES / "mail" / f"{time.time_ns()}.json").write_text(json.dumps({
            "from": msg["From"], "to": to, "subject": msg["Subject"],
            "text": plain.get_content() if plain else "", "html": html.get_content() if html else ""},
            ensure_ascii=False), encoding="utf-8")
        return {}

    def quit(self):
        return 221, b"Bye"

    close = quit


class GmailIMAP:
    """The butler's inbox, empty: no student has replied yet."""

    def __init__(self, host="", port=993, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if not _gmail_ok(user, password):
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        return "OK", [b"logged in"]

    def select(self, mailbox="INBOX", readonly=False):
        return "OK", [b"0"]

    def uid(self, command, *args):
        return "OK", [b""]

    def logout(self):
        return "BYE", [b""]


def open_browser(url, *args, **kwargs) -> bool:
    """A page that would open on screen. Google's consent page is answered at once: the fake
    redirects to Butler's waiting listener, which this follows, in the background, because the
    listener only starts reading once this returns."""
    FAKES.mkdir(parents=True, exist_ok=True)
    with open(FAKES / "browser.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"url": url}) + "\n")
    google = config().get("google_api", "")
    if google and str(url).startswith(google + "/auth"):
        def visit():
            time.sleep(0.5)
            try:
                urllib.request.urlopen(url, timeout=30).read()
            except OSError:
                pass
        threading.Thread(target=visit, daemon=True).start()
    return True


def install() -> None:
    cfg = config()
    if not cfg:
        return
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ["ANTHROPIC_BASE_URL"] = cfg["claude"]
    os.environ["BUTLER_GOOGLE_API"] = cfg["google_api"]
    os.environ["BUTLER_GOOGLE_CLIENT_ID"] = cfg["google_client_id"]
    os.environ["BUTLER_GOOGLE_CLIENT_SECRET"] = cfg["google_client_secret"]
    os.environ["BUTLER_UPDATE_MANIFEST"] = cfg["manifest"]
    smtplib.SMTP_SSL = GmailSMTP
    imaplib.IMAP4_SSL = GmailIMAP
    webbrowser.open = webbrowser.open_new = webbrowser.open_new_tab = open_browser


try:
    install()
except Exception as e:  # noqa: BLE001 - a broken hook must show, but never stop Butler starting
    import sys
    print(f"(skill eval hook failed: {type(e).__name__}: {e})", file=sys.stderr)
