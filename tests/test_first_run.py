"""The guided first run (scripts/first-run.sh) end to end, with scripted keyboard input: what a
student sees after the download, from the welcome to the hand-off to Claude Code.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import json
import unittest

from fakes.account_api import FakeAccountAPI
from fakes.setup_home import SetupHome

LESSONS = ['butler town "Lisbon, Portugal"', "butler adults Sam Alex", "butler brief", "butler claude", ""]


class FirstRunTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeAccountAPI(auto_verify=True).start()
        self.addCleanup(self.api.stop)

    def home(self, api="default", name="Bento") -> SetupHome:
        h = SetupHome(self.api if api == "default" else api, butler_name=name)
        self.addCleanup(h.close)
        return h

    def test_the_whole_first_run(self):
        h = self.home()
        proc = h.first_run(["", "", "sam@example.com", "yes", *LESSONS])
        out = proc.stdout
        self.assertEqual(proc.returncode, 0, out + proc.stderr)
        self.assertIn("Hello! I'm Bento, your new butler.", out)
        self.assertIn("You're signed in as sam@example.com.", out)
        self.assertIn("Bento is ready.", out)
        self.assertIn("I work for Sam and Alex.", out)
        self.assertIn("Your brief is ready", out)
        self.assertIn("It starts with Module 1: Meet Claude Code.", out)
        self.assertIn("starting Module 1 of the Build Your Butler course", out)
        # Weekly lesson emails only because they said yes.
        self.assertEqual(self.api.prefs, [{"email": "sam@example.com", "nudges": True}])
        # The session went to Keychain, never to a file.
        token = h.secrets().get("butler-account")
        self.assertTrue(token)
        for f in h.root.rglob("*"):
            if f.is_file() and f.suffix in {".yaml", ".txt", ".log", ".md", ".json", ""}:
                self.assertNotIn(token, f.read_text(errors="replace"), str(f))
        s = h.settings()
        self.assertEqual((s["name"], s["timezone"]), ("Bento", "Europe/Lisbon"))
        request = self.api.requests[next(iter(self.api.requests))]
        self.assertEqual(request["butler_name"], "Bento")
        # The Terms, agreed to here and sent with the sign-in.
        self.assertIn("By continuing, you agree to the Terms (https://buildyourbutler.com/terms/)", out)
        version = (h.root / "TERMS_VERSION").read_text().strip()
        terms = json.loads((h.root / "data" / "terms.json").read_text())
        self.assertEqual(terms["terms_version"], version)
        self.assertRegex(terms["terms_accepted_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual((request["terms_version"], request["terms_accepted_at"]),
                         (version, terms["terms_accepted_at"]))
        lessons = [p["lesson"] for p in self.api.progress]
        for lesson in ("setup.email", "setup.name", "setup.town", "setup.first-brief", "setup.claude"):
            self.assertIn(lesson, lessons)
        # `butler` works from any new Terminal window.
        self.assertIn(f'export PATH="{h.root}/bin:$PATH"', (h.home / ".zprofile").read_text())
        self.assertNotIn("Traceback", out + proc.stderr)

    def test_declining_the_terms_exits_kindly(self):
        h = self.home()
        proc = h.first_run(["", "n"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Nothing has been sent anywhere", proc.stdout)
        self.assertEqual(self.api.calls, [])
        self.assertFalse((h.root / "data" / "terms.json").exists())
        self.assertFalse((h.root / "config" / "settings.yaml").exists())
        # The way back it names works: `butler` is on the PATH of any new Terminal window.
        self.assertIn("open a new Terminal window and type: butler setup", proc.stdout)
        self.assertIn(f'export PATH="{h.root}/bin:$PATH"', (h.home / ".zprofile").read_text())

    def test_the_terms_are_asked_once_per_version(self):
        h = self.home()
        h.first_run(["", "", "skip", *LESSONS[:3], ""])
        again = h.first_run(["", "skip", *LESSONS[:3], ""])
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertNotIn("By continuing", again.stdout)
        (h.root / "TERMS_VERSION").write_text("0.2\n")
        newer = h.first_run(["", "", "skip", *LESSONS[:3], ""])
        self.assertIn("By continuing", newer.stdout)
        self.assertEqual(json.loads((h.root / "data" / "terms.json").read_text())["terms_version"], "0.2")

    def test_terms_agreed_offline_reach_the_server_later(self):
        h = self.home(api=None)
        h.first_run(["", "", "sam@example.com"])
        accepted = json.loads((h.root / "data" / "terms.json").read_text())["terms_accepted_at"]
        h.env["BUTLER_API"] = self.api.url + "/api"
        h.env["BUTLER_GEOCODE_URL"] = self.api.url + "/geocode"
        proc = h.first_run(["", "sam@example.com", "", *LESSONS])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("By continuing", proc.stdout)
        # Pressing Enter at the weekly-email question means no.
        self.assertEqual(self.api.prefs, [{"email": "sam@example.com", "nudges": False}])
        request = self.api.requests[next(iter(self.api.requests))]
        self.assertEqual(request["terms_accepted_at"], accepted)

    def test_rename_at_the_welcome(self):
        h = self.home()
        proc = h.first_run(["Jeeves", "", "skip", *LESSONS])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(h.settings()["name"], "Jeeves")
        self.assertEqual((h.root / ".butler-name").read_text().strip(), "Jeeves")

    def test_no_name_from_the_download_asks_for_one(self):
        h = self.home(name=None)
        proc = h.first_run(["Bad!Name", "Alfred", "", "skip", *LESSONS])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("What would you like to call me?", proc.stdout)
        self.assertIn("That one won't work.", proc.stdout)
        self.assertEqual(h.settings()["name"], "Alfred")

    def test_offline_still_reaches_the_first_brief(self):
        h = self.home(api=None)
        proc = h.first_run(["", "", "sam@example.com", *LESSONS[:3], ""])
        out = proc.stdout
        self.assertEqual(proc.returncode, 1, out)   # the town lookup needs the internet
        self.assertIn("I can't reach buildyourbutler.com right now", out)
        self.assertIn("Bento is ready.", out)
        self.assertNotIn("Traceback", out + proc.stderr)

    def test_skip_email_then_finish(self):
        h = self.home()
        proc = h.first_run(["", "", "skip", *LESSONS])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Sign in later with: butler setup", proc.stdout)
        self.assertEqual(h.secrets(), {})
        self.assertEqual(self.api.requests, {})

    def test_a_wrong_paste_is_explained(self):
        h = self.home()
        proc = h.first_run(["", "", "skip", "rm -rf ~", 'butler adults Sam', *LESSONS])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertGreaterEqual(proc.stdout.count("Paste the line above, starting with: butler town"), 2)
        self.assertTrue(h.home.exists())

    def test_bad_email_then_good(self):
        h = self.home()
        proc = h.first_run(["", "", "not-an-email", "sam@example.com", "", *LESSONS])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("doesn't look like an email address", proc.stdout)
        self.assertIn("You're signed in as sam@example.com.", proc.stdout)

    def test_sign_in_down_carries_on_without_a_retry_loop(self):
        api = FakeAccountAPI(unavailable=True).start()
        self.addCleanup(api.stop)
        h = self.home(api=api)
        proc = h.first_run(["", "", "sam@example.com", *LESSONS])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Sign-in isn't available right now. That's on our side, not yours.", proc.stdout)
        self.assertEqual(proc.stdout.count("Your email \u203a"), 1, "it asked for the email only once")
        self.assertIn("Bento is ready.", proc.stdout)
        self.assertIn("Your brief is ready", proc.stdout)
        self.assertEqual(h.secrets(), {})

    def test_stop_before_claude(self):
        h = self.home()
        proc = h.first_run(["", "", "skip", *LESSONS[:3], ""])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("When you're ready, open Terminal and type: butler claude", proc.stdout)


if __name__ == "__main__":
    unittest.main()
