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
        for group in ("Getting started:", "Every day:", "When something's wrong:"):
            self.assertIn(group, proc.stdout)

    def test_a_mistyped_command_says_so_in_plain_words_and_lists_the_commands(self):
        for args, first in ((["fly"], 'I don\'t know "butler fly". Check the spelling.'),
                            (["update", "--now"], "That's not quite how butler update works.")):
            proc = self.home.butler(*args)
            self.assertEqual(proc.returncode, 1, args)
            self.assertIn(first, proc.stdout)
            self.assertIn("  butler doctor ", proc.stdout)
            for jargon in ("usage:", "error:", "invalid choice", "unrecognized arguments"):
                self.assertNotIn(jargon, proc.stdout + proc.stderr, args)

    def test_butler_alone_before_the_first_run_ends_says_how_to_pick_it_up(self):
        proc = self.home.butler()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Your butler's first run isn't finished yet.", proc.stdout)
        self.assertIn("To pick up where you left off, type: butler setup", proc.stdout)
        self.assertIn("  butler setup ", proc.stdout, "then the commands")

    def test_butler_alone_says_where_you_are_and_the_one_thing_next(self):
        self.home.butler("name", "Bento")
        self.home.butler("town", "Lisbon")
        self.home.butler("adults", "Sam", "Alex")
        (self.home.root / "data" / "first-run-done").write_text("2026-10-12T08:00:00Z\n")
        out = self.home.butler().stdout
        self.assertIn("Bento is set up through Module 0, Your first two minutes.", out)
        self.assertIn("Next: Module 1, Meet Claude Code. Type: butler claude", out)
        (self.home.root / "data" / "claude-welcomed").touch()
        out = self.home.butler().stdout
        self.assertIn("Bento is set up through Module 1, Meet Claude Code.", out)
        self.assertIn("Next: Module 2, Hire your butler properly. Type: butler claude", out)
        self.assertIn("The lesson page: https://buildyourbutler.com/course/hire-your-butler/", out)
        # The calendar step passes; Module 2 hasn't: the next one is still Module 2.
        (self.home.root / "bin" / "butler-ek").write_text("#!/bin/sh\n")
        settings = self.home.root / "config" / "settings.yaml"
        settings.write_text(settings.read_text().replace("include: []", "include:\n  - Johnson Family"))
        self.assertIn("Next: Module 2, Hire your butler properly.", self.home.butler().stdout)

    def test_claude_mode_round_trips_and_explains_itself(self):
        proc = self.home.butler("claude-mode")
        self.assertIn("Claude Code is set to Ask first: Claude checks with you before each change.", proc.stdout)
        proc = self.home.butler("claude-mode", "auto")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Done. Auto: Claude acts more on its own", proc.stdout)
        self.assertIn("It starts the next time you type: butler claude", proc.stdout)
        self.assertEqual(self.home.settings()["claude_mode"], "auto")
        self.assertIn("Claude Code is set to Auto", self.home.butler("claude-mode").stdout)
        self.home.butler("claude-mode", "ASK")
        self.assertEqual(self.home.settings()["claude_mode"], "ask")
        bad = self.home.butler("claude-mode", "yolo")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("Choose ask or auto, like this: butler claude-mode ask", bad.stdout)
        self.assertEqual(self.home.settings()["claude_mode"], "ask")

    def test_claude_opens_in_the_chosen_mode(self):
        for mode, flag in (("ask", "--permission-mode default"), ("auto", "--permission-mode auto")):
            self.home.butler("claude-mode", mode)
            proc = self.home.butler("claude", "--yes")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn(f"would run: claude {flag} \"", proc.stdout)
        self.assertIn("that I chose auto mode", proc.stdout, "the welcome doesn't promise asking")
        self.assertNotIn("you'll always ask", proc.stdout)

    def test_claude_asks_the_mode_once(self):
        proc = self.home.butler("claude", stdin="\n2\n")       # yes, install; 2, auto
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("One choice before Claude Code opens. How should it work with you?", proc.stdout)
        self.assertIn("1. Ask first: Claude checks with you before each change. (Recommended while you learn.)",
                      proc.stdout)
        self.assertIn("Auto it is. You can change it any time: butler claude-mode ask", proc.stdout)
        self.assertIn("--permission-mode auto", proc.stdout)
        self.assertEqual(self.home.settings()["claude_mode"], "auto")
        again = self.home.butler("claude", stdin="\n")
        self.assertNotIn("One choice", again.stdout)
        self.assertIn("--permission-mode auto", again.stdout)

    def test_claude_with_enter_is_ask_first(self):
        proc = self.home.butler("claude", stdin="\n\n")
        self.assertIn("Ask first it is.", proc.stdout)
        self.assertIn("--permission-mode default", proc.stdout)
        self.assertIn("you'll always ask before changing anything", proc.stdout)

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

    def test_unknown_town_says_what_to_try(self):
        proc = self.home.butler("town", "Nowhereville")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("add your state or country after a comma", proc.stdout)
        self.assertNotIn("Portugal", proc.stdout)

    def test_town_with_a_state_picks_that_state_and_says_so(self):
        """The course's own examples (Module 0): the biggest Springfield and Paris are the wrong ones."""
        proc = self.home.butler("town", "Springfield, Illinois")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Got it: Springfield, Illinois, United States. Your timezone is America/Chicago, and "
                      "weather comes in Fahrenheit.", proc.stdout)
        self.assertEqual(self.home.settings()["weather"]["latitude"], 39.8)
        proc = self.home.butler("town", "Springfield")
        self.assertIn("Got it: Springfield, Missouri, United States.", proc.stdout)
        self.assertIn('Not the right Springfield? Add the state or country, like this: '
                      'butler town "Springfield, Illinois"', proc.stdout)
        for words in (proc.stdout, self.home.butler("town", "Paris, Texas").stdout):
            self.assertNotIn("Portugal", words)

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

    def test_status_and_progress_say_done_and_to_do_in_words(self):
        """VoiceOver skips a lone "·", so the words carry it; a screen reader gets only words."""
        self.home.butler("town", "Lisbon")
        out = self.home.butler("status").stdout
        self.assertIn("  ✓ Done:  Your town and weather", out)
        self.assertIn("  · To do: Signed in to the course", out)
        for env in ({"BUTLER_PLAIN": "1"}, {"CLAUDE_AX_SCREEN_READER": "1"}, {"BUTLER_FAKE_VOICEOVER": "1"}):
            self.home.env.update(env)
            status, progress = self.home.butler("status").stdout, self.home.butler("progress").stdout
            for k in env:
                del self.home.env[k]
            self.assertIn("  Done:  Your town and weather", status, env)
            self.assertIn("  To do: Signed in to the course", status, env)
            self.assertIn("  Module 3, /butler-setup calendar: not done yet", progress, env)
            for mark in ("✓", "·", "…"):
                self.assertNotIn(mark, status + progress, env)
        self.home.env.update({"BUTLER_PLAIN": "0", "CLAUDE_AX_SCREEN_READER": "1"})
        self.assertIn("✓ Done:", self.home.butler("status").stdout, "BUTLER_PLAIN=0 turns it off")

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


class HelpTest(unittest.TestCase):
    """`butler help` and a mistyped command: one command per line, aligned, grouped (council P8)."""

    COMMANDS = [c for _, group in cli.COMMANDS for c, _ in group]

    def check(self, lines: list[str], width: int) -> None:
        commands = re.compile(r"butler(?: (?:setup|name|town|adults|brief|claude-mode|claude|status|progress|"
                              r"portal|update|doctor|help)\b)?")
        for line in lines:
            self.assertLessEqual(len(line), width, line)
        # Which command each line starts with: the longest one it begins with, then a space or nothing.
        started = []
        for line in lines:
            found = [c for c in self.COMMANDS if line == f"  {c}" or line.startswith(f"  {c} ")]
            if found:
                started.append(max(found, key=len))
        self.assertEqual(sorted(started), sorted(self.COMMANDS), f"at {width} columns")
        for line in lines:
            if line.startswith("  butler"):
                self.assertEqual(len(commands.findall(line.split("  ")[1])), 1, line)

    def test_every_command_starts_its_own_line_at_80_columns(self):
        lines = cli.help_lines(76)
        self.check(lines, 76)
        # Wide enough: the descriptions line up beside the commands.
        columns = {line.index(what) for line in lines for _, group in cli.COMMANDS for c, what in group
                   if line.startswith(f"  {c} ") and what in line}
        self.assertEqual(len(columns), 1, "descriptions aren't aligned")

    def test_every_command_starts_its_own_line_at_40_columns(self):
        self.check(cli.help_lines(36), 36)

    def test_the_help_through_the_command_at_both_widths(self):
        for columns in ("80", "40"):
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"BUTLER_COLUMNS": columns}), redirect_stdout(out):
                self.assertEqual(cli.main(["help"]), 0)
            self.check(out.getvalue().splitlines(), int(columns) - 4)
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"BUTLER_COLUMNS": columns}), redirect_stdout(out), \
                 mock.patch.object(cli.progress, "sync"):
                self.assertEqual(cli.main(["fly"]), 1)
            self.check([l for l in out.getvalue().splitlines() if l.startswith("  ")], int(columns) - 4)

    def test_every_parser_command_is_listed(self):
        source = Path(cli.__file__).read_text()
        parsed = set(re.findall(r'add_parser\("([a-z-]+)"', source))
        listed = {c.split()[1] for c in self.COMMANDS if " " in c}
        self.assertEqual(parsed | {"setup"}, listed, "`butler setup` is bin/butler's own")


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
        self.assertIn("The portal isn't running. Open Claude Code (type: butler claude) and type /butler-doctor.",
                      out.getvalue())
        for jargon in ("run.sh", "launchd"):
            self.assertNotIn(jargon, out.getvalue())
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
