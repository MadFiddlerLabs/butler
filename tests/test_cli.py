"""The `butler` command in a fresh, just-downloaded install: each lesson line does what the course
says, in plain words, and records progress when signed in.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from butler import cli, devices, system
from fakes.account_api import FakeAccountAPI
from fakes.setup_home import REPO, SetupHome


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
        commands = re.compile(r"butler(?!-)(?: (?:setup|name|town|adults|brief|claude-mode|claude|status|progress|"
                              r"resume|portal|update|doctor|secret|help)\b)?")
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


class ResumeTest(unittest.TestCase):
    """`butler resume`, what the Butler app runs: straight to where the student left off, with the
    first run or Claude Code really started (a recorder stands in for each)."""

    STEPS = ["install", "calendar", "brain", "inbox", "replies", "money", "projects", "letters", "phone"]

    def setUp(self):
        self.home = SetupHome(None)
        self.addCleanup(self.home.close)
        self.home.bootstrap()
        self.launched = self.home.stub_claude()
        # The first run, replaced by a recorder: what matters here is that resume starts it.
        self.first_run = self.home.tmp / "first-run-started-by.txt"
        script = self.home.root / "scripts" / "first-run.sh"
        script.write_text(f'#!/bin/bash\necho "first-run.sh $*" > "{self.first_run}"\n')
        script.chmod(0o755)

    def finish_first_run(self, mode: str | None = "ask") -> None:
        self.home.butler("name", "Bento")
        if mode:
            self.home.butler("claude-mode", mode)
        (self.home.root / "data" / "first-run-done").write_text("2026-10-12T08:00:00Z\n")

    def steps_done(self, steps: list[str]) -> None:
        (self.home.root / "data" / "claude-welcomed").touch()
        (self.home.root / "data" / "progress.json").write_text(json.dumps(
            {"steps": {s: "2026-10-13T08:00:00Z" for s in steps}}))

    def claude(self) -> tuple[str, list[str]]:
        """The folder Claude Code started in, and its arguments."""
        lines = self.launched.read_text().splitlines()
        return lines[0], lines[1:]

    def log(self) -> str:
        return (self.home.root / "logs" / "setup.log").read_text()

    def test_before_the_first_run_ends_it_resumes_the_first_run(self):
        proc = self.home.butler("resume")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.first_run.read_text(), "first-run.sh \n", "no question first: straight there")
        self.assertFalse(self.launched.exists())
        self.assertIn("resume: the first run", self.log())

    def test_before_python_exists_it_resumes_the_first_run_too(self):
        shutil.rmtree(self.home.root / ".venv")
        proc = self.home.butler("resume")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.first_run.read_text(), "first-run.sh \n")
        self.assertIn("resume: the first run", self.log())

    def test_after_the_first_run_it_opens_claude_code_on_module_1(self):
        self.finish_first_run(mode=None)
        proc = self.home.butler("resume", stdin="\n")        # Enter: ask first, as `butler claude` asks
        out = proc.stdout
        self.assertEqual(proc.returncode, 0, out + proc.stderr)
        self.assertIn("Bento is set up through Module 0, Your first two minutes.", out)
        self.assertIn("One choice before Claude Code opens.", out)
        self.assertIn("Opening Claude Code for Module 1, Meet Claude Code. If it asks you to sign in, "
                      "follow the link it shows.", out)
        self.assertNotIn("Opening Claude Code in your Butler folder", out, "said once, not twice")
        folder, argv = self.claude()
        self.assertEqual(Path(folder).resolve(), self.home.root.resolve())
        self.assertEqual(argv, ["--permission-mode", "default",
                                cli.FIRST_CLAUDE_PROMPT.format(manners=cli.MANNERS["ask"])])
        self.assertTrue((self.home.root / "data" / "claude-welcomed").exists())
        self.assertIn("resume: Claude Code, Module 1", self.log())
        self.assertFalse(self.first_run.exists())

    def test_in_the_course_it_opens_claude_code_on_the_next_step_in_the_chosen_mode(self):
        self.finish_first_run(mode="auto")
        self.steps_done(["install", "calendar"])
        proc = self.home.butler("resume")
        out = proc.stdout
        self.assertEqual(proc.returncode, 0, out + proc.stderr)
        self.assertIn("Bento is set up through Module 3, Show your butler your calendar.", out)
        self.assertIn("Opening Claude Code for Module 4, Give your butler a brain.", out)
        self.assertEqual(self.claude()[1], ["--permission-mode", "auto", cli.NEXT_CLAUDE_PROMPT])
        self.assertIn("resume: Claude Code, Module 4", self.log())
        # `butler claude` starts it exactly the same way.
        self.launched.unlink()
        self.home.butler("claude")
        self.assertEqual(self.claude()[1], ["--permission-mode", "auto", cli.NEXT_CLAUDE_PROMPT])

    def test_a_skipped_step_is_still_next(self):
        self.finish_first_run()
        self.steps_done(["install", "calendar", "brain", "inbox", "replies", "projects"])   # no money
        out = self.home.butler("resume").stdout
        self.assertIn("Bento is set up through Module 6, Talk to your butler.", out)
        self.assertIn("Opening Claude Code for Module 7, Your butler keeps the books.", out)

    def test_with_every_step_done_it_asks_what_to_change(self):
        self.finish_first_run()
        self.steps_done(self.STEPS)
        proc = self.home.butler("resume")
        out = proc.stdout
        self.assertEqual(proc.returncode, 0, out + proc.stderr)
        self.assertIn("Bento is set up through Module 11, Your butler on your phone. Well done.", out)
        self.assertIn("Opening Claude Code, so you can tell it what you'd like to change.", out)
        self.assertEqual(self.claude()[1], ["--permission-mode", "default", cli.DONE_CLAUDE_PROMPT])
        self.assertIn("resume: Claude Code, every step done", self.log())
        self.launched.unlink()
        self.home.butler("claude")
        self.assertEqual(self.claude()[1], ["--permission-mode", "default", cli.DONE_CLAUDE_PROMPT])

    def test_without_claude_code_it_offers_to_install_it_as_butler_claude_does(self):
        self.finish_first_run()
        (self.home.fakebin / "claude").unlink()
        self.home.env["BUTLER_CLAUDE_DRY_RUN"] = "1"
        proc = self.home.butler("resume", stdin="no\n")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Install Claude Code now?", proc.stdout)
        self.assertIn("When you're ready, type: butler claude", proc.stdout)

    def test_butler_alone_names_the_one_step_way_back(self):
        self.finish_first_run()
        out = self.home.butler().stdout
        self.assertIn("To go straight there in one step, open the Butler app, or type: butler resume", out)
        self.assertIn("  butler resume ", out)
        self.assertFalse(self.launched.exists(), "`butler` alone only tells")

    def test_the_app_runs_butler_resume(self):
        command = (REPO / "scripts" / "butler.command").read_text()
        self.assertIn('/../bin/butler" resume', command)
        self.assertIn("Butler/scripts/butler.command", (REPO / "helpers" / "butler-app" / "main.swift").read_text())


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


class SecretTest(unittest.TestCase):
    """`butler secret <name>`: a key or password into the password store, typed hidden, never on a
    command line, in a file or on screen. Windows' way, where store-secret.sh's Mac dialog isn't."""

    def secret(self, *args: str, typed=("sk-invented 0000 0000",), refused=None):
        out = io.StringIO()
        with mock.patch.object(cli.getpass, "getpass", side_effect=typed) as asked, \
             mock.patch.object(cli.config, "store_secret", side_effect=refused) as store, \
             mock.patch.object(cli.progress, "sync") as sync, redirect_stdout(out):
            code = cli.main(["secret", *args])
        sync.assert_not_called()
        return code, out.getvalue(), asked, store

    def test_the_key_goes_to_the_password_store_without_its_spaces(self):
        code, out, asked, store = self.secret("butler-anthropic")
        self.assertEqual(code, 0, out)
        store.assert_called_once_with("butler-anthropic", "sk-invented00000000")
        self.assertEqual(asked.call_args.args[0], "Your Claude API key \u203a ")
        self.assertIn(f"Stored butler-anthropic in {system.PASSWORD_STORE}.", out)
        self.assertNotIn("invented", out)

    def test_only_butlers_own_secrets_and_never_from_the_command_line(self):
        for args in ((), ("butler-account",), ("sk-invented-0000",)):
            code, out, asked, store = self.secret(*args)
            self.assertEqual(code, 1, args)
            self.assertIn("butler secret butler-anthropic", out)
            asked.assert_not_called()
            store.assert_not_called()
        with redirect_stdout(io.StringIO()), mock.patch.object(cli.progress, "sync"):
            self.assertEqual(cli.main(["secret", "butler-gmail", "sk-invented-0000"]), 1, "no value as an argument")

    def test_empty_cancelled_or_refused_stores_nothing(self):
        code, out, _, store = self.secret("butler-gmail", typed=("   ",))
        self.assertEqual((code, "That was empty. Nothing was stored." in out), (1, True))
        store.assert_not_called()
        for interrupted in (EOFError, KeyboardInterrupt):
            code, out, _, store = self.secret("butler-gmail", typed=interrupted)
            self.assertEqual((code, "Cancelled. Nothing was stored." in out), (1, True))
            store.assert_not_called()
        code, out, _, _ = self.secret("butler-lunchmoney", refused=OSError("Credential Manager is locked"))
        self.assertEqual(code, 1)
        self.assertIn(f"{system.PASSWORD_STORE} didn't take it. Nothing was stored.", out)


class WindowsCommandTest(unittest.TestCase):
    """What the `butler` command does differently on Windows, checked on any computer."""

    def windows(self):
        return mock.patch.object(cli.system, "NAME", "windows")

    def mac(self):
        return mock.patch.object(cli.system, "NAME", "mac")

    def test_setup_runs_the_python_first_run_on_windows(self):
        with self.windows(), mock.patch("butler.first_run.main", return_value=0) as first_run, \
             self.assertRaises(SystemExit) as done:
            cli.main(["setup", "--again"])
        first_run.assert_called_once_with(["--again"])
        self.assertEqual(done.exception.code, 0)

    def test_setup_on_a_mac_is_first_run_sh(self):
        with self.mac(), mock.patch.object(cli.os, "execv") as execv, mock.patch.object(cli.progress, "sync") as sync:
            cli.main(["setup"])
        sync.assert_not_called()
        script = str(cli.ROOT / "scripts" / "first-run.sh")
        execv.assert_called_once_with(script, [script])

    def test_the_brief_and_the_claude_code_installer(self):
        with self.windows():
            self.assertEqual(cli._preview(), [sys.executable, "-X", "utf8", str(cli.ROOT / "run.py"), "preview"])
            self.assertEqual(cli._installer(), ("irm https://claude.ai/install.ps1 | iex",
                                                ["powershell", "-NoProfile", "-Command",
                                                 "irm https://claude.ai/install.ps1 | iex"]))
        with self.mac():
            self.assertEqual(cli._preview(), [str(cli.ROOT / "run.sh"), "preview"])
            self.assertEqual(cli._installer(), (cli.CLAUDE_INSTALL, ["bash", "-c", cli.CLAUDE_INSTALL]))

    def test_claude_code_is_found_where_its_installer_puts_it(self):
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, True)
        (home / ".local" / "bin").mkdir(parents=True)
        (home / ".local" / "bin" / "claude.exe").touch()
        with self.windows(), mock.patch.object(cli.shutil, "which", return_value=None), \
             mock.patch.object(cli.Path, "home", return_value=home):
            self.assertEqual(cli._claude_path(), str(home / ".local" / "bin" / "claude.exe"))

    def test_claude_code_runs_as_a_child_on_windows_and_its_exit_code_comes_back(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        env = {k: v for k, v in os.environ.items() if k != "BUTLER_CLAUDE_DRY_RUN"}
        with self.windows(), mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(cli, "_claude_path", return_value="C:\\claude.exe"), \
             mock.patch.object(cli, "CLAUDE_WELCOMED", tmp / "claude-welcomed"), \
             mock.patch.object(cli.ss, "load", return_value={"claude_mode": "ask"}), \
             mock.patch.object(cli.progress, "report"), mock.patch.object(cli.os, "chdir"), \
             mock.patch.object(cli.os, "execv") as execv, mock.patch.object(cli.subprocess, "run") as run, \
             redirect_stdout(io.StringIO()):
            run.return_value.returncode = 3
            self.assertEqual(cli.open_claude(), 3)
        execv.assert_not_called()
        self.assertEqual(run.call_args.args[0][:3], ["C:\\claude.exe", "--permission-mode", "default"])


class ButlerCmdTest(unittest.TestCase):
    """bin/butler.cmd, the `butler` command on Windows."""

    CMD = REPO / "bin" / "butler.cmd"

    def test_it_runs_butlers_python_in_utf8_mode_through_run_py(self):
        text = self.CMD.read_bytes()
        self.assertNotRegex(text, rb"[^\r]\n", "a .cmd needs CRLF line ends (.gitattributes)")
        self.assertIn(rb'set "BUTLER_PYTHON=%~dp0..\.venv\Scripts\python.exe"', text)
        self.assertIn(rb'"%BUTLER_PYTHON%" -X utf8 "%~dp0..\run.py" cli %*', text)

    @unittest.skipUnless(os.name == "nt", "cmd.exe runs it")
    def test_the_lessons_through_butler_cmd(self):
        api = FakeAccountAPI().start()
        self.addCleanup(api.stop)
        h = SetupHome(api)
        self.addCleanup(h.close)
        butler = str(h.root / "bin" / "butler.cmd")

        def run(*args):
            return subprocess.run([butler, *args], cwd=h.home, env=h.env, capture_output=True, text=True,
                                  encoding="utf-8", timeout=300)

        before = run("help")
        self.assertEqual(before.returncode, 1)
        self.assertIn("Butler isn't installed completely yet.", before.stdout)
        h.bootstrap()
        proc = run("help")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("The butler command.", proc.stdout)
        proc = run("town", "Lisbon, Portugal")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Got it: Lisbon, Lisbon, Portugal.", proc.stdout)
        self.assertEqual(h.settings()["timezone"], "Europe/Lisbon")
        self.assertEqual(run("fly").returncode, 1)


if __name__ == "__main__":
    unittest.main()
