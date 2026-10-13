"""The `butler` command in a fresh, just-downloaded install: each lesson line does what the course
says, in plain words, and records progress when signed in.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import io
import json
import os
import re
import shutil
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from butler import cli, devices
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

    def test_progress_waits_while_signed_out_then_goes_with_the_next_command(self):
        self.home.butler("town", "Lisbon")
        self.assertEqual(self.api.progress, [], "not signed in: nothing sent")
        out = self.home.butler("progress").stdout
        self.assertIn("· Module 3 · /butler-setup calendar: not done yet", out)
        self.assertIn("Also waiting: setup.town", out)
        self.assertIn("You're not signed in", out)
        self.assertIn("nothing from your household", out)

        # The calendar step's check passes: the helper is there and a calendar is chosen.
        (self.home.root / "bin" / "butler-ek").write_text("#!/bin/sh\n")
        settings = self.home.root / "config" / "settings.yaml"
        settings.write_text(settings.read_text().replace("include: []", "include:\n  - Johnson Family"))
        self.assertIn("… Module 3 · /butler-setup calendar: done, waiting to send", self.home.butler("progress").stdout)

        self.sign_in()
        self.home.butler("status")
        lessons = [p["lesson"] for p in self.api.progress]
        self.assertEqual(lessons[0], "setup.town")
        self.assertEqual(lessons[1:], [l for l in lessons[1:] if l.startswith("m3-")])
        self.assertEqual(len(lessons), 6, lessons)
        self.assertIn("✓ Module 3 · /butler-setup calendar: sent", self.home.butler("progress").stdout)
        self.assertEqual(len(self.api.progress), 6, "nothing goes twice")

    def test_doctor_on_a_fresh_install(self):
        proc = self.home.butler("doctor")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("looks healthy", proc.stdout)


class PortalCommandTest(unittest.TestCase):
    """`butler portal` pairs this Mac's browser. The one-time code travels in an owner-only file,
    never on a command line, where other accounts on the Mac could read it."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        for p in (mock.patch.object(devices, "FILE", self.dir / "portal_devices.json"),
                  mock.patch.object(cli, "PORTAL_OPENER", self.dir / "portal-open.html"),
                  mock.patch("butler.config.load_settings", return_value={"portal": {"port": 8799}}),
                  mock.patch.object(cli.ss, "load", return_value={"name": "Bento"}),
                  mock.patch.object(cli.progress, "sync"),
                  mock.patch.dict(os.environ, {"BUTLER_COLUMNS": "1000"})):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("BUTLER_NO_OPEN", None)

    def test_says_so_when_the_portal_isnt_running(self):
        out = io.StringIO()
        with mock.patch.object(cli, "_listening", return_value=False), redirect_stdout(out):
            self.assertEqual(cli.main(["portal"]), 1)
        self.assertIn("The portal isn't running. Start it with ./run.sh portal", out.getvalue())
        self.assertFalse(cli.PORTAL_OPENER.exists())

    def test_opens_an_owner_only_page_that_pairs_once(self):
        out = io.StringIO()
        with mock.patch.object(cli, "_listening", return_value=True), \
             mock.patch.object(cli.subprocess, "run") as run, redirect_stdout(out):
            self.assertEqual(cli.main(["portal"]), 0)
        self.assertEqual(run.call_args.args[0], ["open", str(cli.PORTAL_OPENER)])
        self.assertIn("Bento's portal is open in your browser, and this browser is paired.", out.getvalue())
        self.assertEqual(cli.PORTAL_OPENER.stat().st_mode & 0o777, 0o600)
        page = cli.PORTAL_OPENER.read_text()
        self.assertIn('action="http://127.0.0.1:8799/pair"', page)
        code = re.search(r'name="code" value="([A-Z0-9-]+)"', page).group(1)
        self.assertNotIn(code, out.getvalue())
        self.assertTrue(devices.pair(code, "Mac, Safari"))
        self.assertIsNone(devices.pair(code, "Mac, Safari"))


if __name__ == "__main__":
    unittest.main()
