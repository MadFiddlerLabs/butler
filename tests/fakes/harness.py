"""Runs one Butler flow inside a test home with every outside service faked, and records what
went out: emails, Claude prompts, helper calls, URLs and portal responses.

    python harness.py <flow> <record.json>      (cwd: the test home; PYTHONPATH: its src/)

Flows: daily, daily-dry, brief-check, replies, weekly, monthly, portal, progress, tick. The fakes:

- Claude: `llm._request` (the API call, after ID numbers are withheld) answers from canned responses. Each schema is checked to be a valid,
  strict JSON schema, and each canned answer is validated against it, so schema drift fails.
- Gmail: smtplib.SMTP_SSL records messages; imaplib.IMAP4_SSL serves fakes/imap/*.eml.
- Lunch Money, Open-Meteo: urllib.request.urlopen answers from fixtures.py. Loopback addresses
  (the fake account API a test started) go through.
- News: feedparser.parse returns invented stories, one carrying a prompt injection.
- Keychain: a fake `security` on PATH (installed by the test home).
- Anything else that tries the network fails loudly.

BUTLER_FAKE_DOWN takes services down for the failure tests, comma-separated: lunchmoney, weather,
news, claude (an API error 500), claude-timeout, smtp (Gmail refuses the login), imap (Gmail's
IMAP is unreachable). The stub butler-ek reads it too: calendar makes it fail.

BUTLER_FAKE_BUG plants a bug a release could ship, for the update's brief check: money (the money
section raises a TypeError) or render (the email's HTML can't be made).
"""

import io
import json
import os
import re
import socket
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures  # noqa: E402

HOME = Path.cwd()
DOWN = set(filter(None, os.environ.get("BUTLER_FAKE_DOWN", "").split(",")))
RECORD = {"mails": [], "smtp_logins": [], "prompts": [], "schema_errors": [], "urls": [],
          "imap_seen": [], "portal": {}, "error": None}


# ---------- network guard ----------

def _no_network(*args, **kwargs):
    raise OSError(f"network access in a test: {args[:1]}")


def _loopback(address) -> bool:
    """This machine itself: the fake servers other tests start (tests/fakes/account_api.py).
    The guard is installed process-wide when a test imports this module, so it must let them in."""
    return isinstance(address, tuple) and address[0] in ("127.0.0.1", "localhost", "::1")


_real_create_connection = socket.create_connection
_real_connect = socket.socket.connect
socket.create_connection = lambda address, *a, **k: (
    _real_create_connection(address, *a, **k) if _loopback(address) else _no_network(address))
socket.socket.connect = lambda self, address, *a, **k: (
    _real_connect(self, address) if _loopback(address) else _no_network(address))


# ---------- Gmail ----------

class FakeSMTP:
    def __init__(self, host, port=0, timeout=None):
        assert host == "smtp.gmail.com", host

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        RECORD["smtp_logins"].append({"user": user, "password_ok": password == fixtures.SECRETS["butler-gmail"]})
        if "smtp" in DOWN:
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted.")

    def send_message(self, msg):
        plain = msg.get_body(("plain",))
        html = msg.get_body(("html",))
        RECORD["mails"].append({
            "from": msg["From"], "to": [a.strip() for a in msg["To"].split(",")], "subject": msg["Subject"],
            "in_reply_to": msg["In-Reply-To"] or "", "text": plain.get_content() if plain else "",
            "html": html.get_content() if html else "",
            "images": [p.get_filename() for p in msg.walk() if p.get_content_maintype() == "image"]})


class FakeIMAP:
    """Serves fakes/imap/*.eml; the seen flags persist in fakes/imap-seen.json between runs."""
    folder = HOME / "fakes" / "imap"
    seen_file = HOME / "fakes" / "imap-seen.json"

    def __init__(self, host, port=993):
        assert host == "imap.gmail.com", host
        if "imap" in DOWN:
            raise ConnectionRefusedError(61, "Connection refused")
        self.messages = {str(i + 1): p.read_bytes() for i, p in enumerate(sorted(self.folder.glob("*.eml")))} \
            if self.folder.exists() else {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _seen(self) -> list[str]:
        return json.loads(self.seen_file.read_text()) if self.seen_file.exists() else []

    def login(self, user, password):
        assert password == fixtures.SECRETS["butler-gmail"], "IMAP login without the Keychain password"
        return "OK", [b"logged in"]

    def select(self, mailbox="INBOX", readonly=False):
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        command = command.lower()
        if command == "search":
            unseen = [u for u in self.messages if u not in self._seen()]
            return "OK", [" ".join(unseen).encode()]
        if command == "fetch":
            uid = args[0].decode() if isinstance(args[0], bytes) else args[0]
            return "OK", [(f"{uid} (RFC822 {{{len(self.messages[uid])}}}".encode(), self.messages[uid]), b")"]
        if command == "store":
            seen = self._seen() + [args[0].decode() if isinstance(args[0], bytes) else args[0]]
            self.seen_file.write_text(json.dumps(sorted(set(seen))))
            RECORD["imap_seen"] = sorted(set(seen))
            return "OK", [b""]
        raise AssertionError(f"unexpected IMAP command {command}")

    def logout(self):
        return "BYE", [b""]


# ---------- Lunch Money, weather ----------

_real_urlopen = urllib.request.urlopen
LUNCH_MONEY = "api.lunchmoney.dev"
# v2 validates query parameters strictly and answers 400 to any it doesn't know.
LUNCH_MONEY_PARAMS = {"transactions": {"start_date", "end_date", "limit", "offset"},
                      "categories": {"format", "is_group"}, "manual_accounts": set(), "plaid_accounts": set()}


def _lunch_money(path: str, query: dict, url: str) -> dict:
    """Lunch Money's v2 API, in the shapes its OpenAPI spec gives (lunchmoney.dev/v2/openapi)."""
    if path not in LUNCH_MONEY_PARAMS:
        raise AssertionError(f"unexpected Lunch Money path {path}")
    unknown = set(query) - LUNCH_MONEY_PARAMS[path]
    if unknown:
        raise urllib.error.HTTPError(url, 400, f"unknown query parameters {sorted(unknown)}", {},
                                     io.BytesIO(b"{}"))
    if path == "transactions":
        txs = fixtures.transactions(date.fromisoformat(query["start_date"]), date.fromisoformat(query["end_date"]))
        offset, limit = int(query.get("offset", 0)), int(query.get("limit", 1000))
        assert 1 <= limit <= 2000, f"Lunch Money's limit is 1 to 2000, not {limit}"
        return {"transactions": txs[offset:offset + limit], "has_more": len(txs) > offset + limit}
    if path == "categories":
        flat = query.get("format") == "flattened"
        return {"categories": fixtures.CATEGORIES if flat else fixtures.categories_nested()}
    if path == "manual_accounts":
        return {"manual_accounts": fixtures.MANUAL_ACCOUNTS}
    return {"plaid_accounts": fixtures.PLAID_ACCOUNTS}


def fake_urlopen(req, timeout=None, **kwargs):
    url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
    RECORD["urls"].append(re.sub(r"\?.*", "", url))
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname in ("127.0.0.1", "localhost"):
        # A fake server a test started on this machine (tests/fakes/account_api.py).
        return _real_urlopen(req, timeout=timeout, **kwargs)
    query = dict(urllib.parse.parse_qsl(parsed.query))
    service = {LUNCH_MONEY: "lunchmoney", "api.open-meteo.com": "weather"}.get(parsed.netloc)
    if service in DOWN:
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, io.BytesIO(b"{}"))
    if parsed.netloc == LUNCH_MONEY:
        auth = req.headers.get("Authorization") if isinstance(req, urllib.request.Request) else None
        assert auth == f"Bearer {fixtures.SECRETS['butler-lunchmoney']}", "Lunch Money call without the token"
        assert parsed.path.startswith("/v2/"), f"Lunch Money call off the v2 API: {parsed.path}"
        body = _lunch_money(parsed.path.removeprefix("/v2/"), query, url)
    elif parsed.netloc == "api.open-meteo.com":
        body = fixtures.WEATHER
    else:
        raise OSError(f"network access in a test: {url}")
    return io.BytesIO(json.dumps(body).encode())


def fake_feedparse(url, **kwargs):
    RECORD["urls"].append(url)
    if "news" in DOWN:
        return SimpleNamespace(bozo=1, entries=[])
    return SimpleNamespace(bozo=0, entries=fixtures.feed_entries(datetime.now().astimezone()))


# ---------- Claude ----------

def _strict_problems(schema: dict, path: str = "$") -> list[str]:
    """Structured outputs need every object closed and every property required."""
    problems = []
    if schema.get("type") == "object" or "properties" in schema:
        props = set(schema.get("properties", {}))
        if schema.get("additionalProperties") is not False:
            problems.append(f"{path}: additionalProperties must be false")
        if set(schema.get("required", [])) != props:
            problems.append(f"{path}: required must list every property")
        for name, sub in schema.get("properties", {}).items():
            problems += _strict_problems(sub, f"{path}.{name}")
    if isinstance(schema.get("items"), dict):
        problems += _strict_problems(schema["items"], f"{path}[]")
    return problems


def _refs(prompt: str) -> list[str]:
    return re.findall(r'"ref": "([^"]+)"', prompt)


def _canned(name: str, prompt: str) -> dict:
    if name == "news":
        return {"stories": [{"ref": r, "headline": f"Invented headline {r}", "summary": "An invented summary."}
                            for r in _refs(prompt)[:3]]}
    if name == "prep":
        refs = _refs(prompt)
        return {"prep": [{"ref": refs[0], "note": "Bring the renewal form."}] if refs else []}
    if name == "prep+phrase":
        refs = _refs(prompt)
        return {"prep": [{"ref": refs[0], "note": "Bring the renewal form."}] if refs else [],
                "phrase": {"phrase": "Bom dia", "translation": "Good morning", "why": "at school"}}
    if name == "actions":
        subject = re.search(r'\(subject "([^"]*)"\)', prompt)
        canned = json.loads((HOME / "fakes" / "actions.json").read_text()) \
            if (HOME / "fakes" / "actions.json").exists() else {}
        return {"actions": canned.get(subject.group(1) if subject else "", [])}
    if name == "answer":
        return {"answer": "Invented answer."}
    if name == "weekly":
        return {"headline": "An invented, calm week.", "focus": [{"who": "Sam", "what": "Book the boiler service."}],
                "ideas": [{"title": "Cancel one streaming service", "detail": "Invented.", "per_month": 13, "kind": "cut"}],
                "project_notes": [{"project": "Home", "note": "On track."}]}
    if name == "monthly":
        return {"headline": "An invented month.", "accomplishments": "Several things got done.",
                "time_insight": "Swimming took two hours a week.", "money_insight": "Groceries ran a little high.",
                "next_month": [{"who": "Alex", "what": "Renew the residence card."}]}
    raise AssertionError(f"no canned answer for {name}")


def _schema_name(schema: dict) -> str:
    props = set(schema.get("properties", {}))
    for name, keys in [("news", {"stories"}), ("prep+phrase", {"prep", "phrase"}), ("prep", {"prep"}),
                       ("actions", {"actions"}), ("answer", {"answer"}),
                       ("weekly", {"headline", "focus", "ideas", "project_notes"}),
                       ("monthly", {"headline", "accomplishments", "time_insight", "money_insight", "next_month"})]:
        if props == keys:
            return name
    return "unknown:" + ",".join(sorted(props))


def _claude_down():
    """The SDK's own error types, as a failing API call raises them."""
    import anthropic
    if "claude-timeout" in DOWN:
        error = anthropic.APITimeoutError.__new__(anthropic.APITimeoutError)
        Exception.__init__(error, "Request timed out.")
    else:
        error = anthropic.InternalServerError.__new__(anthropic.InternalServerError)
        Exception.__init__(error, "Internal server error")
        error.status_code = 500
    return error


def fake_request(settings, system, prompt, schema):
    import jsonschema
    name = _schema_name(schema)
    RECORD["prompts"].append({"schema": name, "system": system, "prompt": prompt})
    if DOWN & {"claude", "claude-timeout"}:
        raise _claude_down()
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
        RECORD["schema_errors"] += [f"{name}: {p}" for p in _strict_problems(schema)]
        answer = _canned(name, prompt)
        jsonschema.validate(answer, schema)
    except Exception as e:
        RECORD["schema_errors"].append(f"{name}: {e}")
        raise
    return answer


# ---------- flows ----------

def install_fakes() -> None:
    import feedparser
    import imaplib
    import smtplib
    smtplib.SMTP_SSL = FakeSMTP
    imaplib.IMAP4_SSL = FakeIMAP
    urllib.request.urlopen = fake_urlopen
    feedparser.parse = fake_feedparse
    from butler import llm
    llm._request = fake_request


def run(flow: str) -> None:
    from butler.config import load_settings, tz
    settings = load_settings()
    today = datetime.now(tz(settings)).date()
    if flow in ("daily", "daily-dry"):
        from butler import daily
        sys.argv = ["daily"] + (["--dry-run"] if flow == "daily-dry" else [])
        daily.main()
    elif flow == "brief-check":
        from butler import daily, finance
        bug = os.environ.get("BUTLER_FAKE_BUG")
        if bug == "money":
            def broken(*args, **kwargs):
                return None + 1
            finance.build = broken
        elif bug == "render":
            def broken_html(*args, **kwargs):
                raise AttributeError("'Brief' object has no attribute 'agenda_lines'")
            daily.to_html = broken_html
        RECORD["check"] = daily.brief_check()
    elif flow == "replies":
        from butler import replies
        replies.process(settings)
    elif flow == "weekly":
        from butler import weekly
        weekly.run(settings, weekly.next_letter_day(settings, today), dry_run=False)
    elif flow == "monthly":
        from butler import monthly
        monthly.run(settings, (today.replace(day=1) - timedelta(days=1)).replace(day=1), preview=False)
    elif flow == "portal":
        from butler import portal, projects
        client = portal.app.test_client()
        host = {"Host": "127.0.0.1:8787"}
        post = {**host, "Origin": "http://127.0.0.1:8787"}
        step = next(s for p in projects.load() for s in p.steps if s.status != "done")
        gets = ["/", f"/brief/{today}", "/week", "/manifest.json", "/month", "/money", "/budget",
                "/projects", "/info", "/lists", "/chart/mtd.png", "/chart/categories.png"]
        from butler.budget import budget_file
        gets.append(f"/budget/{budget_file().name}")
        gets += [f"/out/{p.name}" for p in sorted((HOME / "out").glob("*.png"))
                 if re.fullmatch(r"(chart|weekly|monthly)-[\w-]+\.png", p.name)][:3]
        for path in gets:
            r = client.get(path, headers=host)
            RECORD["portal"][f"GET {path}"] = {"status": r.status_code,
                                               "cache_control": r.headers.get("Cache-Control", ""),
                                               "body": r.get_data(as_text=True) if r.mimetype == "text/html" else ""}
        for path, form in [("/lists/add", {"list": "Shopping", "title": "Bread"}),
                           ("/lists/complete", {"id": "r3"}),
                           (f"/projects/{step.id}", {"action": "checked"}),
                           (f"/projects/{step.id}/update", {"text": "Called them, waiting to hear back."})]:
            r = client.post(path, data=form, headers=post)
            RECORD["portal"][f"POST {path}"] = {"status": r.status_code, "cache_control": "", "body": ""}
        r = client.post("/lists/add", data={"list": "Shopping", "title": "x"},
                        headers={**host, "Origin": "https://attacker.example.test"})
        RECORD["portal"]["POST /lists/add cross-site"] = {"status": r.status_code, "cache_control": "", "body": ""}
    elif flow == "tick":
        # What launchd runs every five minutes. The fakes are the network, so the Mac is online.
        from butler import daily, tick
        daily._online = lambda: True
        tick.main()
    elif flow == "progress":
        from butler import progress
        state, problem = progress.sync(force=True)
        RECORD["progress"] = {"state": state, "problem": problem}
    else:
        raise SystemExit(f"unknown flow {flow}")


def main() -> None:
    flow, out = sys.argv[1], Path(sys.argv[2])
    install_fakes()
    try:
        run(flow)
    except BaseException as e:      # record it; the test decides whether it was expected
        if not (isinstance(e, SystemExit) and not e.code):
            RECORD["error"] = "".join(traceback.format_exception(e))
    out.write_text(json.dumps(RECORD, default=str))


if __name__ == "__main__":
    os.environ.setdefault("TZ", "Europe/Lisbon")
    main()
