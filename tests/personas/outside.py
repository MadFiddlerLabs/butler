"""The world outside Butler, for a persona test that goes past Module 2: the services a student
signs up for, and the windows and pages that open on screen. What a person does on those sites
in several clicks is one on_screen action, and the result is a short, plain description with
the values the site would show (fake ones, made up for this run).

- Claude Console (Module 4): a spend limit, and an API key shown once. Butler's own Claude calls
  go through a relay on this machine, which accepts only the keys shown here and forwards to the
  real API, so a key typed wrong fails as it really would. Its cost counts in the run's budget.
- Google (Module 5, and the calendar on Windows): a new Gmail account, 2-Step Verification, an
  app password (refused until 2-Step is on, as Google does); FakeGoogle for Calendar and Tasks.
- Lunch Money (Module 7): an account with a bank connected, and an access token.
- Tailscale (Module 11): installing it on the computer and the phone, the admin console, and the
  phone's browser, which reaches the portal the way `tailscale serve` would.
- Email: the persona's inbox has Butler's mail (written by butler_hook.py) as well as the
  course's, and a reply goes to Butler's IMAP inbox, DKIM-signed by the persona's domain.

Butler's processes load butler_hook.py, which points them here; the stubs (stubs/) stand in for
osascript's dialogs, the Calendar helper and the tailscale command.
"""
import json
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import make_msgid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import events  # noqa: E402
from fakes import fixtures  # noqa: E402
from fakes.google_api import SCOPES, FakeGoogle  # noqa: E402
from screens import page_text  # noqa: E402

WINDOWS = sys.platform == "win32"
FAKES = events.FAKES
ANTHROPIC = "https://api.anthropic.com"
# Dollars per million tokens (input, output) by model family; cache reads cost a tenth of input,
# cache writes a quarter more.
FAMILY_PRICES = {"opus": (5.0, 25.0), "sonnet": (2.0, 10.0), "haiku": (1.0, 5.0)}
TAILNET = "tail4e2a1.ts.net"
# Google's descriptions of the scopes (its OAuth scopes list), so the persona weighs the lessons against them.
SCOPE_NAMES = {SCOPES[0]: "See and download any calendar you can access using your Google Calendar",
               SCOPES[1]: "View and edit events on all your calendars",
               SCOPES[2]: "Create, edit, organise and delete your tasks"}


def price(model: str, usage: dict) -> float:
    family = next((f for f in FAMILY_PRICES if f in (model or "")), "opus")
    inp, out = FAMILY_PRICES[family]
    return (usage.get("input_tokens", 0) * inp + usage.get("output_tokens", 0) * out
            + (usage.get("cache_read_input_tokens") or 0) * inp * 0.1
            + (usage.get("cache_creation_input_tokens") or 0) * inp * 1.25) / 1e6


def usage_of(body: bytes) -> tuple[str, dict]:
    """(model, usage) from a Messages API answer, plain JSON or a stream of events."""
    text = body.decode("utf-8", errors="replace")
    if text.lstrip().startswith("{"):
        data = json.loads(text)
        return data.get("model", ""), data.get("usage", {})
    model, usage = "", {}
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:])
        except ValueError:
            continue
        if event.get("type") == "message_start":
            model = event["message"].get("model", "")
            usage.update(event["message"].get("usage", {}))
        elif event.get("type") == "message_delta":
            usage.update({k: v for k, v in event.get("usage", {}).items() if v is not None})
    return model, usage


class Relay:
    """Butler's Claude calls, to the real API: only with a key the Console showed the persona, and
    only while the run has budget left (`allowed()`). The run's own key replaces the persona's."""

    def __init__(self, allowed):
        self.keys: set[str] = set()
        self.cost = 0.0
        self.calls = 0
        self.allowed = allowed
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, body: bytes, headers: dict | None = None):
                self.send_response(code)
                for k, v in (headers or {"content-type": "application/json"}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _error(self, code: int, kind: str, message: str):
                self._send(code, json.dumps({"type": "error", "error": {"type": kind, "message": message}}).encode())

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.headers.get("x-api-key") not in relay.keys:
                    return self._error(401, "authentication_error", "invalid x-api-key")
                if not relay.allowed():
                    return self._error(400, "invalid_request_error",
                                       "Your credit balance is too low to access the Anthropic API.")
                headers = {k: v for k, v in self.headers.items()
                           if k.lower() in ("content-type", "anthropic-version", "anthropic-beta", "accept")}
                req = urllib.request.Request(ANTHROPIC + self.path, data=body, method="POST",
                                             headers={**headers, "x-api-key": os.environ["ANTHROPIC_API_KEY"]})
                try:
                    with urllib.request.urlopen(req, timeout=600) as r:
                        code, answer, kind = r.status, r.read(), r.headers.get("content-type", "application/json")
                except urllib.error.HTTPError as e:
                    code, answer, kind = e.code, e.read(), e.headers.get("content-type", "application/json")
                if code == 200:
                    model, usage = usage_of(answer)
                    relay.cost += price(model, usage)
                    relay.calls += 1
                self._send(code, answer, {"content-type": kind})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        self._server.shutdown()


def _code(n: int, alphabet: str = string.ascii_lowercase + string.digits) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(n))


class Outside:
    def __init__(self, persona_name: str, email: str, allowed, butler: Path):
        self.name, self.email, self.butler = persona_name, email, butler
        shutil.rmtree(FAKES, ignore_errors=True)
        (FAKES / "bin").mkdir(parents=True)
        (FAKES / "imap").mkdir()
        self.relay = Relay(allowed)
        self.google = FakeGoogle().start()
        self.cfg = {"relay": self.relay.url, "google_api": self.google.url,
                    "google_client_id": self.google.env()["BUTLER_GOOGLE_CLIENT_ID"],
                    "google_client_secret": self.google.env()["BUTLER_GOOGLE_CLIENT_SECRET"],
                    "manifest": str(FAKES / "manifest.json"), "gmail": {"address": None, "app_passwords": []},
                    "lunchmoney_tokens": []}
        self.save()
        self.state = {"console_limit": None, "two_step": False, "lunchmoney": False, "tailscale_phone": False}
        self.mails_seen: set[str] = set()
        self.last_read: dict | None = None
        self.replies = 0
        self._stubs()

    def save(self) -> None:
        (FAKES / "config.json").write_text(json.dumps(self.cfg, indent=2), encoding="utf-8")

    def stop(self) -> None:
        self.relay.stop()
        self.google.stop()

    # ---------- Butler's side ----------
    def _stubs(self) -> None:
        """osascript (its dialogs come to the persona) goes first on PATH; the Calendar helper and
        Tailscale are put in place later, when Butler or Tailscale is installed."""
        if not WINDOWS:
            self._wrapper(FAKES / "bin" / "osascript", "osascript.py")

    @staticmethod
    def _wrapper(path: Path, stub: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'#!/bin/bash\nexec "{sys.executable}" "{HERE / "stubs" / stub}" "$@"\n')
        path.chmod(0o755)

    def path(self, env: dict) -> dict:
        if not WINDOWS:
            env["PATH"] = f"{FAKES / 'bin'}:{env.get('PATH', '')}"
        return env

    def after_install(self) -> None:
        """Once Butler is installed: the Calendar helper (Mac), and an update list that says this
        is the newest version, so no real release replaces the test build."""
        version = (self.butler / ".butler-version").read_text().strip() if (self.butler / ".butler-version").exists() else "0"
        (FAKES / "manifest.json").write_text(json.dumps({"version": version, "windows": {"url": "file:///none"}}))
        # The test build (unsigned) has no prebuilt helper, as a release has. Without the stub,
        # Claude builds the real one, which waits for a permission window the runner never shows.
        helper = self.butler / "bin" / "butler-ek"
        if not WINDOWS:
            helper.unlink(missing_ok=True)
            self._wrapper(helper, "butler_ek.py")

    def hook(self) -> bool:
        """The start-up hook in Butler's venv, once its Python exists (the Mac's first run makes it
        in the background, and an update may make a new one). True when it was just added."""
        venv = self.butler / ".venv"
        sites = list(venv.glob("Lib/site-packages")) + list(venv.glob("lib/python3*/site-packages"))
        if not sites or (sites[0] / "butler-persona.pth").exists():
            return False
        (sites[0] / "butler-persona.pth").write_text(f"import sys; sys.path.append({str(HERE)!r}); import butler_hook\n")
        return True

    # ---------- email ----------
    def mails(self) -> list[dict]:
        out = []
        for path in sorted((FAKES / "mail").glob("*.json")) if (FAKES / "mail").exists() else []:
            mail = json.loads(path.read_text(encoding="utf-8"))
            if self.email in [a.lower() for a in mail["to"]]:
                out.append({**mail, "id": path.stem})
        return out

    def inbox(self, course: list[str]) -> str:
        mails = self.mails()
        lines = [f"{i}. From your butler ({m['from']}): \"{m['subject']}\"" + ("" if m["id"] in self.mails_seen else "  (new)")
                 for i, m in enumerate(mails, 1)]
        if not lines and not course:
            return "Your inbox has nothing new from Build Your Butler or your butler."
        return "\n\n".join(filter(None, ["\n".join(lines) and "Your inbox:\n" + "\n".join(lines)
                                         + "\n(read_email with the number reads one.)", *course]))

    def read(self, which: str) -> str:
        mails = self.mails()
        if not mails:
            return "There's no email from your butler in your inbox."
        m = re.search(r"\d+", which or "")
        mail = mails[min(int(m[0]), len(mails)) - 1] if m else mails[-1]
        self.mails_seen.add(mail["id"])
        self.last_read = mail
        body = mail["text"] or page_text(mail["html"])
        return f"From: {mail['from']}\nTo: {', '.join(mail['to'])}\nSubject: {mail['subject']}\n\n{body[:6000]}"

    def reply(self, text: str) -> str:
        mail = self.last_read or (self.mails() or [None])[-1]
        if not mail:
            return "There's no email from your butler to reply to."
        msg = EmailMessage()
        subject = mail["subject"] if mail["subject"].lower().startswith("re:") else f"Re: {mail['subject']}"
        msg["From"], msg["To"], msg["Subject"] = f"{self.name} <{self.email}>", mail["from"], subject
        msg["Message-ID"] = make_msgid(domain=self.email.split("@")[1])
        if mail.get("message_id"):
            msg["In-Reply-To"] = msg["References"] = mail["message_id"]
        msg.set_content(text + "\n\n" + "\n".join("> " + line for line in (mail["text"] or "").splitlines()[:40]))
        raw = fixtures.gmail_auth_results(self.email.split("@")[1]).encode() + msg.as_bytes()
        self.replies += 1
        (FAKES / "imap" / f"{time.time_ns()}.eml").write_bytes(raw)
        return f"You reply to \"{mail['subject']}\" and click Send. It's on its way to {mail['from']}."

    def wait(self, minutes: str) -> str:
        """Time passes for real (Butler's schedule checks its inbox every five minutes), until a new
        email from the butler arrives."""
        m = re.search(r"\d+", minutes or "")
        limit = min(int(m[0]) if m else 5, 15) * 60
        before = len(self.mails())
        start = time.monotonic()
        while time.monotonic() - start < limit and len(self.mails()) == before:
            time.sleep(10)
        waited = round((time.monotonic() - start) / 60)
        new = len(self.mails()) - before
        return f"You wait about {waited} minute(s)." + (f" A new email from your butler arrives ({new})." if new else "")

    # ---------- the browser ----------
    def browser(self, url: str) -> tuple[str, dict | None]:
        """A page Butler opened: what the persona sees, and the window it must answer (or None)."""
        if url.startswith(self.google.url):
            return (f"Your browser opens Google: \"Choose an account\" ({self.email}), then \"Butler wants access to "
                    "your Google Account\".", {"title": "Sign in with Google: Butler wants access to your Google Account",
                                                "text": "Select what Butler can access: " + "; ".join(
                                                    f"[ ] {SCOPE_NAMES[s]}" for s in SCOPES),
                                                "box": False, "boxes": [SCOPE_NAMES[s] for s in SCOPES],
                                                "buttons": ["Cancel", "Continue"]})
        if re.match(r"^https://(www\.)?buildyourbutler\.com", url):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "persona-test"}), timeout=20) as r:
                    return f"Your browser opens {url}:\n" + page_text(r.read().decode("utf-8", errors="replace"))[:4000], None
            except OSError as e:
                return f"Your browser opens {url}, but it doesn't load ({e}).", None
        return f"Your browser opens {url}.", None

    def google_answer(self, url: str, answer: dict) -> str:
        """The persona's answer on Google's consent page, then the redirect back to Butler."""
        ticked = set(answer.get("ticked") or [])
        self.google.deny = answer.get("button", "").lower() != "continue"
        self.google.granted = {s for s in SCOPES if SCOPE_NAMES[s] in ticked} or set()
        try:
            opener = urllib.request.build_opener(_NoRedirect)
            try:
                opener.open(url, timeout=20)
                return "Google's page shows an error."
            except urllib.error.HTTPError as e:
                location = e.headers.get("Location")
            if not location:
                return "Google says: \"Something went wrong\"."
            with urllib.request.urlopen(location, timeout=30) as r:
                return "The page says: " + page_text(r.read().decode("utf-8", errors="replace"))[:600]
        except OSError as e:
            return f"The page doesn't load ({e})."

    # ---------- the sites ----------
    def site(self, action: str, what: str, text: str) -> str:
        if action == "console_set_limit":
            amount = re.search(r"\d+(?:\.\d+)?", text or what or "")
            if not amount:
                return "Claude Console > Settings > Limits: the box for a monthly limit is empty. Type an amount."
            self.state["console_limit"] = float(amount[0])
            return (f"Claude Console (platform.claude.com, signed in with your Claude account) > Settings > Limits: your "
                    f"monthly spend limit is now ${amount[0]}. You'll get an email if it's reached.")
        if action == "console_create_key":
            key = "sk-ant-api03-" + _code(40, string.ascii_letters + string.digits + "-_") + "AA"
            self.relay.keys.add(key)
            return (f"Claude Console > API keys > Create key. Name: {(text or 'Butler')[:40]!r}. The key appears, once:\n\n"
                    f"    {key}\n\nIt won't be shown again. [Copy] copies it.")
        if action == "gmail_create_account":
            local = re.sub(r"@.*", "", (text or "").strip().lower())
            if not re.fullmatch(r"[a-z0-9][a-z0-9.]{4,28}[a-z0-9]", local):
                return ("Google: \"Sorry, your username must be between 6 and 30 characters long\" and use only letters, "
                        "numbers and periods. Type another.")
            if local.replace(".", "") in ("butler", "mybutler", "familybutler", "homebutler", "thebutler"):
                return "Google: \"That username is taken. Try another.\""
            self.cfg["gmail"]["address"] = f"{local}@gmail.com"
            self.state["two_step"] = False
            self.save()
            return (f"You create a new Google account: name, birthday, the address {local}@gmail.com, a password you "
                    "make up, your phone number for recovery. Gmail opens: the new inbox is empty. You're signed in to "
                    "it in this browser.")
        if action == "google_two_step":
            if not self.cfg["gmail"]["address"]:
                return "You're signed in to your own Google account. (Your butler's Gmail account doesn't exist yet.)"
            self.state["two_step"] = True
            return (f"myaccount.google.com > Security ({self.cfg['gmail']['address']}): 2-Step Verification. Google sends a "
                    "code to your phone, you type it, and click Turn on. \"2-Step Verification is on.\"")
        if action == "google_app_password":
            if not self.cfg["gmail"]["address"]:
                return "You're signed in to your own Google account. (Your butler's Gmail account doesn't exist yet.)"
            if not self.state["two_step"]:
                return "myaccount.google.com/apppasswords: \"The setting you are looking for is not available for your account.\""
            password = _code(16, string.ascii_lowercase)
            self.cfg["gmail"]["app_passwords"].append(password)
            self.save()
            shown = " ".join(password[i:i + 4] for i in range(0, 16, 4))
            return (f"App passwords ({self.cfg['gmail']['address']}): App name {(text or 'Butler')[:30]!r}, Create. \"Your "
                    f"app password for your device\":\n\n    {shown}\n\nIt won't be shown again. [Done]")
        if action == "lunchmoney_sign_up":
            self.state["lunchmoney"] = True
            return ("lunchmoney.app: you start the free trial with your email, pick your primary currency, and connect "
                    "your bank through Plaid (you choose the bank and sign in there). Two accounts appear, with this "
                    "month's transactions. (On this test computer the bank holds an invented family's sample data.)")
        if action == "lunchmoney_create_token":
            if not self.state["lunchmoney"]:
                return "lunchmoney.app asks you to sign in. You don't have a Lunch Money account yet."
            token = _code(50, "0123456789abcdef")
            self.cfg["lunchmoney_tokens"].append(token)
            self.save()
            return (f"Lunch Money > Settings > Developers: Request new access token, label {(text or 'Butler')[:30]!r}. "
                    f"The token:\n\n    {token}\n\nIt's shown only now.")
        if action == "privacy_settings":
            return self.privacy(what + " " + text)
        if action == "tailscale_install":
            return self.tailscale_install(text or what)
        if action == "tailscale_admin":
            return self.tailscale_admin(what, text)
        if action == "phone_open":
            return self.phone(text or what)
        if action == "open_app":
            return self.app(what + " " + text)
        return ""

    def privacy(self, said: str) -> str:
        """System Settings > Privacy & Security on a Mac: Calendars and Reminders, where the lessons'
        fix box sends someone who clicked Don't Allow (bin/butler-ek's stub reads the same file)."""
        if WINDOWS:
            return "Settings > Privacy & security: nothing here is about Butler. (Google's sign-in is in the browser.)"
        path = FAKES / "ek-grants.json"
        grants = json.loads(path.read_text()) if path.exists() else {}
        said = said.lower()
        off = re.search(r"\b(off|deny|disallow|turn off|switch off)\b", said) is not None
        changed = [kind for kind in ("calendar", "reminders") if kind.rstrip("s") in said]
        for kind in changed:
            grants[kind] = not off
        path.write_text(json.dumps(grants))
        rows = "; ".join(f"{k.title()}: butler-ek {'on' if grants.get(k) else 'off'}" for k in ("calendar", "reminders")
                         if k in grants) or "no app has asked for Calendars or Reminders yet"
        return ("System Settings > Privacy & Security" + (f": you switch {'off' if off else 'on'} butler-ek under "
                + " and ".join(k.title() for k in changed) + "." if changed else ".") + f" Now: {rows}.")

    # ---------- Tailscale and the phone ----------
    def _tailscale(self) -> dict:
        path = FAKES / "tailscale.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def tailscale_install(self, where: str) -> str:
        if "phone" in where.lower():
            self.state["tailscale_phone"] = True
            return (f"On your phone: the Tailscale app from the App Store or Google Play, Get started, sign in as "
                    f"{self.email}, allow the VPN. It says Connected.")
        state = {**self._tailscale(), "up": True, "login": self.email, "tailnet": TAILNET}
        state.setdefault("hostname", "macbook" if not WINDOWS else "desktop-4k2j")
        (FAKES / "tailscale.json").write_text(json.dumps(state))
        if WINDOWS:
            _windows_tailscale()
        else:
            app = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
            app.parent.mkdir(parents=True, exist_ok=True)
            self._wrapper(app, "tailscale.py")
        return (f"Tailscale installs; you sign in as {self.email} in the browser and click Connect. Its icon shows "
                f"Connected. This computer is called {state['hostname']} on your tailnet.")

    def tailscale_admin(self, what: str, text: str) -> str:
        state = self._tailscale()
        if not state.get("up"):
            return "login.tailscale.com asks you to sign in: you haven't installed Tailscale yet."
        said = (what + " " + text).lower()
        if "https" in said or "dns" in said or "certificate" in said:
            state["https"] = True
            reply = "Admin console > DNS: MagicDNS is on. HTTPS Certificates: you click Enable HTTPS. It's on."
        elif "name" in said and text.strip():
            state["hostname"] = re.sub(r"[^a-z0-9-]", "", text.strip().lower())[:40] or state["hostname"]
            reply = f"Admin console > Machines > Edit machine name: it's now {state['hostname']}."
        elif "invite" in said or "user" in said:
            reply = "Admin console > Users > Invite users: you type their email and click Send invites."
        else:
            reply = (f"Admin console (login.tailscale.com): Machines lists {state['hostname']}"
                     + (" and your phone." if self.state["tailscale_phone"] else ".") + " Tabs: Machines, Users, DNS, Settings.")
        (FAKES / "tailscale.json").write_text(json.dumps(state))
        return reply

    def phone(self, url: str) -> str:
        state = self._tailscale()
        m = re.match(r"^(?:https://)?([\w.-]+)(/.*)?$", url.strip())
        if not self.state["tailscale_phone"]:
            return "The phone's browser says it can't find the server: the phone isn't on your tailnet."
        if not m or m[1] != f"{state.get('hostname')}.{TAILNET}" or not state.get("serve"):
            return f"The phone's browser says \"Safari can't open the page\" ({url})."
        req = urllib.request.Request(f"http://127.0.0.1:{state['serve']}{m[2] or '/'}",
                                     headers={"Tailscale-User-Login": self.email, "Tailscale-User-Name": self.name,
                                              "Host": f"{state['hostname']}.{TAILNET}", "User-Agent": "Mobile Safari"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return "On your phone:\n" + page_text(r.read().decode("utf-8", errors="replace"))[:4000]
        except urllib.error.HTTPError as e:
            return f"On your phone: {e.code}. " + page_text(e.read().decode("utf-8", errors="replace"))[:1500]
        except OSError as e:
            return f"The phone's browser can't connect ({e})."

    def app(self, which: str) -> str:
        which = which.lower()
        if "task" in which or "google calendar" in which or (WINDOWS and "remind" in which):
            lists = []
            for lst in self.google.lists:
                items = [t["title"] for t in self.google.tasks.get(lst["id"], []) if t.get("status") != "completed"]
                lists.append(f"{lst['title']}: " + (", ".join(items) or "(empty)"))
            return "Google Tasks:\n" + "\n".join(lists)
        if "remind" in which or "calendar" in which:
            log = FAKES / "ek-calls.jsonl"
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            added = [c for c in calls if c["command"] in ("add-reminder", "add-event")]
            shown = "\n".join(f"- {c['command']}: " + " ".join(c["args"]) for c in added) or "(nothing your butler added)"
            return ("Reminders and Calendar show the sample family's lists and events, plus what your butler added:\n" + shown)
        return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _windows_tailscale() -> None:
    """C:\\Program Files\\Tailscale\\tailscale.exe: a small program that runs stubs/tailscale.py,
    built with the C# compiler Windows has."""
    target = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    source = FAKES / "tailscale.cs"
    stub = str(HERE / "stubs" / "tailscale.py").replace("\\", "\\\\")
    python = sys.executable.replace("\\", "\\\\")
    source.write_text(
        "using System.Diagnostics; using System.Linq;\n"
        "class P { static int Main(string[] a) {\n"
        f"  var i = new ProcessStartInfo(\"{python}\", \"\\\"{stub}\\\" \" + string.Join(\" \", a.Select(x => \"\\\"\" + x + \"\\\"\")));\n"
        "  i.UseShellExecute = false; var p = Process.Start(i); p.WaitForExit(); return p.ExitCode; } }\n")
    csc = Path(os.environ["SystemRoot"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    subprocess.run([str(csc), "/nologo", f"/out:{target}", str(source)], check=True, capture_output=True)
