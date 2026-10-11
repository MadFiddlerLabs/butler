"""The `butler` command in a fresh, just-downloaded install: each lesson line does what the course
says, in plain words, and records progress when signed in.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import json
import unittest
import urllib.request

from fakes.account_api import FakeAccountAPI
from fakes.setup_home import SetupHome


class CliTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeAccountAPI().start()
        self.addCleanup(self.api.stop)
        self.home = SetupHome(self.api)
        self.addCleanup(self.home.close)
        self.home.bootstrap()

    def sign_in(self) -> None:
        start = urllib.request.Request(self.api.url + "/api/auth/start", method="POST",
                                       data=json.dumps({"email": "sam@example.com", "butler_name": "Bento"}).encode())
        with urllib.request.urlopen(start) as resp:
            started = json.load(resp)
        self.api.click()
        poll = urllib.request.Request(self.api.url + "/api/auth/poll", method="POST",
                                      data=json.dumps({"request_id": started["request_id"],
                                                       "poll_secret": started["poll_secret"]}).encode())
        with urllib.request.urlopen(poll) as resp:
            token = json.load(resp)["session_token"]
        self.home.keychain.write_text(json.dumps({"butler-account": token}))

    def test_help_without_python_says_to_run_setup(self):
        bare = SetupHome(self.api)
        self.addCleanup(bare.close)
        proc = bare.butler("town", "Lisbon")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("butler setup", proc.stdout)

    def test_help(self):
        proc = self.home.butler("help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn('butler town "Lisbon"', proc.stdout)

    def test_name(self):
        proc = self.home.butler("name", "Mr", "Hudson")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.home.settings()["name"], "Mr Hudson")
        bad = self.home.butler("name", "<bad>")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("won't work", bad.stdout)

    def test_town(self):
        proc = self.home.butler("town", "Lisbon, Portugal")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Europe/Lisbon", proc.stdout)
        s = self.home.settings()
        self.assertEqual((s["timezone"], s["weather"]["unit"]), ("Europe/Lisbon", "celsius"))

    def test_unknown_town_suggests_the_country(self):
        proc = self.home.butler("town", "Nowhereville")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Try adding the country", proc.stdout)

    def test_town_offline(self):
        offline = SetupHome(None)
        self.addCleanup(offline.close)
        offline.bootstrap()
        proc = offline.butler("town", "Lisbon")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("internet connection", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    def test_adults(self):
        proc = self.home.butler("adults", "Sam", "Alex")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("I work for Sam and Alex.", proc.stdout)
        self.assertEqual([a["name"] for a in self.home.settings()["household"]["adults"]], ["Sam", "Alex"])

    def test_brief_needs_town_and_adults_first(self):
        proc = self.home.butler("brief")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("butler adults", proc.stdout)
        self.assertIn("butler town", proc.stdout)

    def test_brief_after_the_lessons_records_progress(self):
        self.sign_in()
        self.home.butler("name", "Bento")
        self.home.butler("town", "Lisbon")
        self.home.butler("adults", "Sam", "Alex")
        proc = self.home.butler("brief")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        html = next((self.home.root / "out" / "preview").glob("brief-*.html")).read_text()
        self.assertIn("Bento", html)
        self.assertIn("Not connected yet", html)
        self.assertNotIn("Johnson", html)
        self.assertEqual([p["lesson"] for p in self.api.progress],
                         ["setup.name", "setup.town", "setup.first-brief"])

    def test_status_points_to_the_next_lesson(self):
        proc = self.home.butler("status")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Next: butler setup", proc.stdout)
        self.sign_in()
        self.home.butler("town", "Lisbon")
        self.assertIn('Next: butler name "Bento"', self.home.butler("status").stdout)

    def test_claude_dry_run_installs_then_opens_the_next_lesson(self):
        proc = self.home.butler("claude", "--yes")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("paid Claude plan", proc.stdout)
        self.assertIn("would run: curl -fsSL https://claude.ai/install.sh | bash", proc.stdout)
        self.assertIn("It starts with Module 1: Meet Claude Code.", proc.stdout)
        self.assertIn("Don't change any files yet.", proc.stdout)

    def test_claude_later_picks_up_where_you_left_off(self):
        (self.home.root / "data").mkdir(exist_ok=True)
        (self.home.root / "data" / "claude-welcomed").touch()
        proc = self.home.butler("claude", "--yes")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('would run:', proc.stdout)
        self.assertIn('"/butler-setup"', proc.stdout)
        self.assertNotIn("Module 1", proc.stdout)

    def test_claude_can_wait(self):
        proc = self.home.butler("claude", stdin="no\n")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("When you're ready, type: butler claude", proc.stdout)

    def test_doctor_on_a_fresh_install(self):
        proc = self.home.butler("doctor")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("looks healthy", proc.stdout)


if __name__ == "__main__":
    unittest.main()
