"""The persona tests' outside world (tests/personas/outside.py, butler_hook.py, stubs/), free:
the fake sites, the start-up hook in a real Butler process, the windows the stubs put on screen,
the Claude relay and Google's consent page. Everything works in a folder of its own, never in
~/persona-fakes.

Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
FOLDER = tempfile.mkdtemp(prefix="persona-fakes-")
os.environ["BUTLER_PERSONA_FAKES"] = FOLDER
sys.path.insert(0, str(HERE / "personas"))
import events  # noqa: E402
import outside  # noqa: E402

POSIX = sys.platform != "win32"


def run_in_butler(code: str, timeout: float = 60) -> subprocess.CompletedProcess:
    """Python as Butler's processes start it on a runner: the hook loaded first."""
    hook = f"import sys; sys.path.append({str(HERE / 'personas')!r}); import butler_hook\n"
    return subprocess.run([sys.executable, "-c", hook + code], capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "PYTHONPATH": str(HERE.parent / "src")})


class OutsideTest(unittest.TestCase):
    def setUp(self):
        self.world = outside.Outside("Walter", "walter.1@example.com", lambda: True, Path(FOLDER) / "Butler")
        self.addCleanup(self.world.stop)

    def test_google_wants_two_step_before_an_app_password(self):
        self.assertIn("taken", self.world.site("gmail_create_account", "", "butler"))
        self.assertIn("6 and 30", self.world.site("gmail_create_account", "", "ab"))
        self.assertIn("penny.walter.butler@gmail.com", self.world.site("gmail_create_account", "", "penny.walter.butler"))
        self.assertIn("not available for your account", self.world.site("google_app_password", "", "Butler"))
        self.world.site("google_two_step", "", "")
        shown = self.world.site("google_app_password", "", "Butler")
        password = "".join(shown.split("\n\n")[1].split())
        self.assertEqual(len(password), 16)
        self.assertEqual(events.config()["gmail"], {"address": "penny.walter.butler@gmail.com", "app_passwords": [password]})

    def test_lunch_money_and_the_console_hand_out_what_the_fakes_accept(self):
        self.assertIn("don't have a Lunch Money account", self.world.site("lunchmoney_create_token", "", "Butler"))
        self.world.site("lunchmoney_sign_up", "", "")
        token = self.world.site("lunchmoney_create_token", "", "Butler").split("\n\n")[1].strip()
        self.assertEqual(events.config()["lunchmoney_tokens"], [token])
        key = self.world.site("console_create_key", "", "Butler").split("\n\n")[1].strip()
        self.assertTrue(key.startswith("sk-ant-api03-"))
        self.assertEqual(self.world.relay.keys, {key})
        self.assertIn("$5", self.world.site("console_set_limit", "", "5"))

    def test_the_hook_points_butler_at_the_fakes(self):
        self.world.site("gmail_create_account", "", "penny.walter.butler")
        self.world.site("google_two_step", "", "")
        password = "".join(self.world.site("google_app_password", "", "").split("\n\n")[1].split())
        self.world.site("lunchmoney_sign_up", "", "")
        token = self.world.site("lunchmoney_create_token", "", "").split("\n\n")[1].strip()
        p = run_in_butler(f"""
import json, os, smtplib, urllib.request, urllib.error, webbrowser
from email.message import EmailMessage
try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login("penny.walter.butler@gmail.com", "wrong")
except smtplib.SMTPAuthenticationError as e:
    print("refused", e.smtp_code)
msg = EmailMessage()
msg["From"], msg["To"], msg["Subject"], msg["Message-ID"] = "penny.walter.butler@gmail.com", "walter.1@example.com", "Good morning", "<1@butler.local>"
msg.set_content("Your day.")
with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
    s.login("Penny.Walter.Butler@gmail.com", {" ".join([password[:4], password[4:]])!r})
    s.send_message(msg)
def lm(tok):
    req = urllib.request.Request("https://api.lunchmoney.dev/v2/manual_accounts", headers={{"Authorization": "Bearer " + tok}})
    try:
        with urllib.request.urlopen(req) as r:
            return sorted(json.loads(r.read()))
    except urllib.error.HTTPError as e:
        return e.code
print("lunchmoney", lm({token!r}), lm("nope"))
webbrowser.open("https://buildyourbutler.com/course/")
print("env", os.environ["ANTHROPIC_BASE_URL"] == {self.world.relay.url!r}, os.environ["BUTLER_GOOGLE_API"] == {self.world.google.url!r})
""")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("refused 535", p.stdout)
        self.assertIn("lunchmoney ['manual_accounts'] 401", p.stdout)
        self.assertIn("env True True", p.stdout)
        [mail] = self.world.mails()
        self.assertEqual((mail["subject"], mail["text"].strip()), ("Good morning", "Your day."))
        self.assertIn("(new)", self.world.inbox([]))
        self.assertIn("Your day.", self.world.read("1"))
        self.assertNotIn("(new)", self.world.inbox([]))
        [(name, event)] = events.waiting(set())
        self.assertEqual(event, {"kind": "browser", "url": "https://buildyourbutler.com/course/"})

    def test_a_reply_reaches_butlers_inbox_signed_by_the_personas_domain(self):
        self.world.site("gmail_create_account", "", "penny.walter.butler")
        self.world.site("google_two_step", "", "")
        password = "".join(self.world.site("google_app_password", "", "").split("\n\n")[1].split())
        (Path(FOLDER) / "mail").mkdir(exist_ok=True)
        (Path(FOLDER) / "mail" / "1.json").write_text(json.dumps({
            "from": "Penny <penny.walter.butler@gmail.com>", "to": ["walter.1@example.com"], "subject": "Good morning",
            "message_id": "<1@butler.local>", "in_reply_to": "", "text": "Your day.", "html": ""}))
        self.assertIn("Penny", self.world.reply("add milk to shopping"))
        p = run_in_butler(f"""
import email, email.policy, imaplib
from butler import inbox
with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
    imap.login("penny.walter.butler@gmail.com", {password!r})
    imap.select("INBOX")
    uids = imap.uid("search", None, "UNSEEN")[1][0].split()
    raw = imap.uid("fetch", uids[0], "(RFC822)")[1][0][1]
msg = email.message_from_bytes(raw, policy=email.policy.default)
print(msg["Subject"], "|", msg["In-Reply-To"], "|", inbox._dkim_passes(msg, "walter.1@example.com"))
print(msg.get_body(("plain",)).get_content().splitlines()[0])
""")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Re: Good morning | <1@butler.local> | True", p.stdout)
        self.assertIn("add milk to shopping", p.stdout)

    def test_wait_ends_when_the_butler_writes(self):
        def write():
            time.sleep(0.5)
            (Path(FOLDER) / "mail").mkdir(exist_ok=True)
            (Path(FOLDER) / "mail" / "2.json").write_text(json.dumps({
                "from": "p@gmail.com", "to": ["walter.1@example.com"], "subject": "Done", "message_id": "",
                "in_reply_to": "", "text": "Added milk.", "html": ""}))
        threading.Thread(target=write).start()
        started = time.monotonic()
        real_sleep = time.sleep
        with unittest.mock.patch.object(outside.time, "sleep", lambda s: real_sleep(0.2)):
            said = self.world.wait("5")
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("A new email", said)

    def test_after_install_the_update_list_says_this_is_the_newest(self):
        butler = Path(FOLDER) / "Butler"
        (butler / "bin").mkdir(parents=True, exist_ok=True)
        (butler / ".butler-version").write_text("0.6.0\n")
        (butler / "bin" / "butler-ek").write_text("binary")
        self.world.after_install()
        self.assertEqual(json.loads((Path(FOLDER) / "manifest.json").read_text())["version"], "0.6.0")
        if POSIX:
            self.assertIn("butler_ek.py", (butler / "bin" / "butler-ek").read_text())
        site = butler / ".venv" / "lib" / "python3.13" / "site-packages"
        site.mkdir(parents=True, exist_ok=True)
        self.assertTrue(self.world.hook())
        self.assertFalse(self.world.hook(), "added once")
        self.assertIn("import butler_hook", (site / "butler-persona.pth").read_text())


class ActionsTest(unittest.TestCase):
    def test_the_persona_can_pick_every_site_action(self):
        """run_persona.py's on_screen tool lists its actions; each one outside.py answers must be in it."""
        import re
        handled = set(re.findall(r'action == "(\w+)"', (HERE / "personas" / "outside.py").read_text()))
        tool = (HERE / "personas" / "run_persona.py").read_text()
        offered = set(re.findall(r'"(\w+)"', re.search(r'\[("check_version"[^\]]*)\]', tool)[1]))
        self.assertGreater(len(handled), 10)
        self.assertLessEqual(handled, offered)


class WindowsOnScreenTest(unittest.TestCase):
    """The stubs ask; something answers the way the harness does for the persona."""

    def setUp(self):
        self.world = outside.Outside("Priya", "priya.2@example.com", lambda: True, Path(FOLDER) / "Butler")
        self.addCleanup(self.world.stop)

    def answer(self, *answers):
        def run():
            seen, left = set(), list(answers)
            deadline = time.monotonic() + 30
            while left and time.monotonic() < deadline:
                for name, event in events.waiting(seen):
                    self.asked.append(event)
                    events.reply(name, left.pop(0))
                time.sleep(0.1)
        self.asked = []
        thread = threading.Thread(target=run)
        thread.start()
        return thread

    def stub(self, name, *args):
        return subprocess.run([sys.executable, str(HERE / "personas" / "stubs" / name), *args],
                              capture_output=True, text=True, timeout=60)

    def test_the_key_window_takes_what_the_persona_pastes(self):
        script = ('text returned of (display dialog "Your Claude API key" with title "Butler" default answer "" '
                  'with hidden answer buttons {"Cancel", "Save"} default button "Save")')
        thread = self.answer({"button": "Save", "text": "sk-ant-api03-abc", "ticked": []})
        p = self.stub("osascript.py", "-e", script)
        thread.join()
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "sk-ant-api03-abc"))
        self.assertEqual(self.asked[0], {"kind": "dialog", "title": "Butler", "text": "Your Claude API key", "box": True,
                                         "hidden": True, "buttons": ["Cancel", "Save"]})
        thread = self.answer({"button": "Cancel", "text": "", "ticked": []})
        p = self.stub("osascript.py", "-e", script)
        thread.join()
        self.assertEqual(p.returncode, 1)
        self.assertIn("User canceled", p.stderr)

    def test_the_calendar_helper_asks_for_calendar_then_reminders_once(self):
        allow = {"button": "Allow Full Access", "text": "", "ticked": []}
        thread = self.answer(allow, allow)
        p = self.stub("butler_ek.py", "reminder-lists")
        thread.join()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Shopping", p.stdout)
        self.assertEqual([e["title"][:30] for e in self.asked], ["“butler-ek” would like full ac"] * 2)
        self.assertIn("Calendar", self.asked[0]["title"])
        self.assertIn("Reminders", self.asked[1]["title"])
        self.answer().join()
        self.assertEqual(self.stub("butler_ek.py", "add-reminder", "--list", "Shopping", "--title", "milk").returncode, 0, "asked once")
        self.assertEqual(self.asked, [])
        self.assertIn("--title milk", self.world.app("Reminders"))
        self.assertFalse((HERE / "fakes" / "ek-calls.jsonl").exists(), "nothing left in the repo")

    def test_a_refused_calendar_fails_like_the_real_helper(self):
        thread = self.answer({"button": "Don't Allow", "text": "", "ticked": []})
        p = self.stub("butler_ek.py", "calendars")
        thread.join()
        self.assertEqual(p.returncode, 1)
        self.assertIn("no Calendar access", p.stderr)

    def test_tailscale_serve_needs_https_first(self):
        self.assertEqual(self.stub("tailscale.py", "status").returncode, 1, "not installed yet")
        (Path(FOLDER) / "tailscale.json").write_text(json.dumps({"up": True, "login": "priya.2@example.com",
                                                                 "tailnet": outside.TAILNET, "hostname": "macbook"}))
        self.assertIn("Enable them", self.stub("tailscale.py", "serve", "--bg", "8787").stderr)
        self.assertIn("Enable HTTPS", self.world.tailscale_admin("turn on HTTPS certificates", ""))
        self.assertIn(f"https://macbook.{outside.TAILNET}/", self.stub("tailscale.py", "serve", "--bg", "8787").stdout)
        self.assertIn("can't find the server", self.world.phone(f"https://macbook.{outside.TAILNET}/"))


class GoogleConsentTest(unittest.TestCase):
    def test_the_persona_answers_googles_page_and_butler_gets_what_was_ticked(self):
        world = outside.Outside("Jordan", "jordan.3@example.com", lambda: True, Path(FOLDER) / "Butler")
        self.addCleanup(world.stop)
        from butler import gcal
        opened, got = [], {}
        with unittest.mock.patch.dict(os.environ, world.google.env()):
            thread = threading.Thread(target=lambda: got.update(scopes=gcal.connect(open_browser=opened.append, wait=20)))
            with unittest.mock.patch.object(gcal.config, "store_secret", lambda *a: None), \
                    unittest.mock.patch.object(gcal, "_remember", lambda tokens: None):
                thread.start()
                deadline = time.monotonic() + 10
                while not opened and time.monotonic() < deadline:
                    time.sleep(0.05)
                screen, form = world.browser(opened[0])
                self.assertIn("Butler wants access", screen)
                self.assertEqual(form["buttons"], ["Cancel", "Continue"])
                said = world.google_answer(opened[0], {"button": "Continue", "text": "",
                                                       "ticked": [outside.SCOPE_NAMES[s] for s in outside.SCOPES]})
                thread.join(20)
        self.assertNotIn("error", said.lower())
        self.assertEqual(got["scopes"], set(), "connect returns what Google didn't grant: nothing")


class RelayTest(unittest.TestCase):
    def test_only_a_key_the_console_showed_gets_through_and_it_is_counted(self):
        seen = []

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                seen.append(self.headers.get("x-api-key"))
                body = json.dumps({"model": "claude-sonnet-5-5", "usage": {"input_tokens": 1000, "output_tokens": 100}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        budget = {"left": True}
        relay = outside.Relay(lambda: budget["left"])
        self.addCleanup(relay.stop)
        relay.keys.add("sk-ant-api03-shown")

        def post(key):
            req = urllib.request.Request(relay.url + "/v1/messages", data=b"{}", method="POST",
                                         headers={"x-api-key": key, "content-type": "application/json"})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code
        with unittest.mock.patch.object(outside, "ANTHROPIC", f"http://127.0.0.1:{server.server_address[1]}"), \
                unittest.mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "the-runs-own-key"}):
            self.assertEqual(post("sk-ant-api03-typo"), 401)
            self.assertEqual(post("sk-ant-api03-shown"), 200)
            budget["left"] = False
            self.assertEqual(post("sk-ant-api03-shown"), 400)
        self.assertEqual(seen, ["the-runs-own-key"], "the persona's key never leaves this machine")
        self.assertAlmostEqual(relay.cost, (1000 * 2 + 100 * 10) / 1e6)

    def test_usage_is_read_from_a_stream_too(self):
        stream = ('event: message_start\ndata: {"type": "message_start", "message": {"model": "claude-opus-5-5", '
                  '"usage": {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 100}}}\n\n'
                  'event: message_delta\ndata: {"type": "message_delta", "usage": {"output_tokens": 50}}\n\n')
        model, usage = outside.usage_of(stream.encode())
        self.assertEqual((model, usage["output_tokens"], usage["cache_read_input_tokens"]), ("claude-opus-5-5", 50, 100))
        self.assertAlmostEqual(outside.price(model, usage), (10 * 5 + 50 * 25 + 100 * 0.5) / 1e6)


if __name__ == "__main__":
    unittest.main()
