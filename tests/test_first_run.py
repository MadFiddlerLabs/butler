"""The guided first run (scripts/first-run.sh) end to end, with scripted keyboard input: what a
student sees after the download, from the welcome to the hand-off to Claude Code.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from butler import account

from fakes.account_api import FakeAccountAPI
from fakes.setup_home import SetupHome

REPO = Path(__file__).resolve().parents[1]
# The town, Enter at "Is that right?", the adults, the brief, then Claude Code (Enter installs it).
LESSONS = ['butler town "Lisbon, Portugal"', "", "butler adults Sam Alex", "butler brief", "butler claude", ""]


class HeardTokenTest(unittest.TestCase):
    """scripts/lib/heard.sh: the number the student types to the token the website takes."""

    def token(self, answer: str) -> str:
        script = f'. "{REPO}/scripts/lib/heard.sh"; heard_token "$1"'
        return subprocess.run(["bash", "-c", script, "bash", answer], capture_output=True, text=True).stdout.strip()

    def test_each_number_is_its_source(self):
        self.assertEqual([self.token(str(n)) for n in range(1, 11)], list(account.HEARD_SOURCES))
        self.assertEqual(self.token(" 4. "), "hacker_news")

    def test_anything_else_is_no_answer(self):
        for answer in ("0", "11", "01", "1x", "reddit", "-1", ""):
            self.assertEqual(self.token(answer), "", answer)


class PlainTest(unittest.TestCase):
    """scripts/lib/plain.sh: when the first run speaks in words only."""

    def plain(self, **env) -> bool:
        fake = {k: v for k, v in os.environ.items() if k not in ("BUTLER_PLAIN", "CLAUDE_AX_SCREEN_READER")}
        fakebin = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, fakebin, True)
        (fakebin / "pgrep").write_text("#!/bin/sh\n[ -n \"$FAKE_VOICEOVER\" ] && [ \"$*\" = '-x VoiceOver' ]\n")
        (fakebin / "pgrep").chmod(0o755)
        fake["PATH"] = f"{fakebin}:/usr/bin:/bin"
        script = f'. "{REPO}/scripts/lib/plain.sh"; butler_plain'
        return subprocess.run(["bash", "-c", script], env={**fake, **env}).returncode == 0

    def test_on_for_a_screen_reader_or_by_asking(self):
        for env in ({"BUTLER_PLAIN": "1"}, {"BUTLER_PLAIN": "yes"}, {"CLAUDE_AX_SCREEN_READER": "1"},
                    {"CLAUDE_AX_SCREEN_READER": "voiceover"}, {"FAKE_VOICEOVER": "1"}):
            self.assertTrue(self.plain(**env), env)

    def test_off_otherwise_or_when_turned_off(self):
        for env in ({}, {"BUTLER_PLAIN": "0"}, {"CLAUDE_AX_SCREEN_READER": "0"}, {"CLAUDE_AX_SCREEN_READER": ""},
                    {"BUTLER_PLAIN": "0", "CLAUDE_AX_SCREEN_READER": "1", "FAKE_VOICEOVER": "1"}):
            self.assertFalse(self.plain(**env), env)

    def test_python_decides_the_same_way(self):
        from butler import config
        cases = [({"BUTLER_PLAIN": "1"}, True), ({"CLAUDE_AX_SCREEN_READER": "1"}, True),
                 ({"BUTLER_PLAIN": "0", "CLAUDE_AX_SCREEN_READER": "1"}, False), ({}, False)]
        for env, want in cases:
            config.plain_output.cache_clear()
            clean = {k: v for k, v in os.environ.items() if k not in ("BUTLER_PLAIN", "CLAUDE_AX_SCREEN_READER")}
            with mock.patch.dict(os.environ, {**clean, **env}, clear=True), \
                 mock.patch.object(config.system, "screen_reader_on", return_value=False):
                self.assertEqual(config.plain_output(), want, env)
        config.plain_output.cache_clear()


class FirstRunTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeAccountAPI(auto_verify=True).start()
        self.addCleanup(self.api.stop)

    def home(self, api="default", name="Bento") -> SetupHome:
        h = SetupHome(self.api if api == "default" else api, butler_name=name)
        self.addCleanup(h.close)
        return h

    def test_a_butler_folder_whose_path_has_a_space_gets_ready(self):
        """Found by tests/e2e/standard-user.sh: in /Users/byb test/Butler, the first run started its
        own scripts/bootstrap.sh through `bash -c` unquoted, ran "/Users/byb", and said "Something
        went wrong getting ready". Here the real bootstrap's place is taken by one that makes the
        test's .venv, so only how it's started is tested."""
        h = self.home()
        spaced = h.tmp / "byb test" / "Butler"
        spaced.parent.mkdir()
        h.root.rename(spaced)
        h.root = spaced
        fake = h.env.pop("BUTLER_BOOTSTRAP")
        (spaced / "scripts" / "bootstrap.sh").write_text(f"#!/bin/bash\nset -e\ncd \"$(dirname \"$0\")/..\"\n{fake}\n")
        proc = h.first_run(["", "", "", "skip", *LESSONS])
        out = proc.stdout
        self.assertNotIn("Something went wrong getting ready", out, out + proc.stderr)
        self.assertIn("Bento is ready.", out)
        self.assertIn("I work for Sam and Alex.", out)
        self.assertTrue((spaced / "data" / "first-run-done").exists(), out + proc.stderr)

    def test_the_whole_first_run(self):
        h = self.home()
        proc = h.first_run(["", "", "3", "sam@example.com", "yes", *LESSONS])
        out = proc.stdout
        self.assertEqual(proc.returncode, 0, out + proc.stderr)
        self.assertIn("Hello! I'm Bento, your new butler.", out)
        # Only claims that hold: household data does leave the Mac, for the services it chooses.
        self.assertIn("We never see your household's data. It goes only from this Mac to services you "
                      "choose, such as Anthropic and Google.", out)
        self.assertNotIn("ever leaves this Mac", out)
        self.assertNotIn("can't break anything", out)
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
        # Nor on a command line, where other accounts on the Mac see it in the process list: not the
        # session token, not the poll secret (scripts/lib/keychain.sh, post() in first-run.sh).
        lines = [" ".join(argv) for argv in h.command_lines()]
        self.assertTrue(any(line.startswith("curl ") for line in lines))
        self.assertIn("security -i", lines)
        self.assertEqual(len(self.api.issued), 2)
        for secret in self.api.issued:
            for line in lines:
                self.assertNotIn(secret, line)
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
        # Where they heard about Butler: a number, saved and sent as its token.
        self.assertIn("Where did you hear about Butler? (optional)", out)
        self.assertIn(" 4. Hacker News", out)
        self.assertEqual(json.loads((h.root / "data" / "heard.json").read_text()), {"heard": "reddit"})
        self.assertEqual(request["heard"], "reddit")
        lessons = [p["lesson"] for p in self.api.progress]
        for lesson in ("setup.email", "setup.name", "setup.town", "setup.first-brief", "setup.claude"):
            self.assertIn(lesson, lessons)
        # Every prompt says what to type; the › marker is never the only cue, and nothing is "below".
        for prompt in ("Name › ", "Press Enter to agree, or type no to stop › ",
                       "Type a number, or press Enter to skip › ", "Your email › ", "Paste here › "):
            self.assertIn(prompt, out)
        self.assertNotRegex(out, r"(^|\n)› ")
        self.assertIn("Paste it at the › prompt", out)
        self.assertNotIn("below", out)
        self.assertNotRegex(out, r"ready\.\.", "no ticker of dots while Python installs")
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
        self.assertFalse((h.root / "data" / "heard.json").exists(), "asked only after the Terms")
        self.assertFalse((h.root / "config" / "settings.yaml").exists())
        # The way back it names works: `butler` is on the PATH of any new Terminal window.
        self.assertIn("open a new Terminal window and type: butler setup", proc.stdout)
        self.assertIn(f'export PATH="{h.root}/bin:$PATH"', (h.home / ".zprofile").read_text())

    def test_the_terms_are_asked_once_per_version(self):
        h = self.home()
        h.first_run(["", "", "", "skip", *LESSONS[:4], ""])
        again = h.first_run(["", "skip", ""])          # the lessons are done: only the email is asked
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertNotIn("By continuing", again.stdout)
        (h.root / "TERMS_VERSION").write_text("0.2\n")
        newer = h.first_run(["", "", "skip", ""])
        self.assertIn("By continuing", newer.stdout)
        self.assertEqual(json.loads((h.root / "data" / "terms.json").read_text())["terms_version"], "0.2")

    def test_terms_and_heard_given_offline_reach_the_server_later(self):
        h = self.home(api=None)
        h.first_run(["", "", "5", "sam@example.com"])
        accepted = json.loads((h.root / "data" / "terms.json").read_text())["terms_accepted_at"]
        h.env["BUTLER_API"] = self.api.url + "/api"
        h.env["BUTLER_GEOCODE_URL"] = self.api.url + "/geocode"
        proc = h.first_run(["", "sam@example.com", "", *LESSONS])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("By continuing", proc.stdout)
        self.assertNotIn("Where did you hear", proc.stdout)
        # Pressing Enter at the weekly-email question means no.
        self.assertEqual(self.api.prefs, [{"email": "sam@example.com", "nudges": False}])
        request = self.api.requests[next(iter(self.api.requests))]
        self.assertEqual(request["terms_accepted_at"], accepted)
        self.assertEqual(request["heard"], "product_hunt")

    def test_where_they_heard_is_asked_once_and_a_skip_sends_nothing(self):
        h = self.home()
        first = h.first_run(["", "", "12", "", "skip"])     # stops at the first lesson
        self.assertIn("Where did you hear about Butler? (optional)", first.stdout)
        self.assertIn("Type a number from 1 to 10, or press Enter to skip.", first.stdout)
        self.assertEqual(json.loads((h.root / "data" / "heard.json").read_text()), {"heard": None})
        again = h.first_run(["", "sam@example.com", ""])
        self.assertNotIn("Where did you hear", again.stdout)
        starts = [body for method, path, body in self.api.calls if path == "/api/auth/start"]
        self.assertEqual(len(starts), 1)
        for body in starts:
            self.assertNotIn("heard", body)

    def test_rename_at_the_welcome(self):
        h = self.home()
        proc = h.first_run(["Jeeves", "", "", "skip", *LESSONS])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(h.settings()["name"], "Jeeves")
        self.assertEqual((h.root / ".butler-name").read_text().strip(), "Jeeves")
        # Written whole, through a file beside it that's gone afterwards: `>` empties the name
        # before writing it, and a window closed in between left none (#37).
        self.assertFalse((h.root / ".butler-name.new").exists())
        self.assertIn('mv -f "$home/.butler-name.new" "$home/.butler-name"', (REPO / "scripts" / "first-run.sh").read_text())

    def test_no_name_from_the_download_asks_for_one(self):
        h = self.home(name=None)
        proc = h.first_run(["Bad!Name", "Alfred", "", "", "skip", *LESSONS])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("What would you like to call me?", proc.stdout)
        self.assertIn("That one won't work.", proc.stdout)
        self.assertEqual(h.settings()["name"], "Alfred")

    def test_offline_still_reaches_the_first_brief(self):
        h = self.home(api=None)
        proc = h.first_run(["", "", "", "sam@example.com", *LESSONS[:4], ""])
        out = proc.stdout
        self.assertEqual(proc.returncode, 1, out)   # the town lookup needs the internet
        self.assertIn("I can't reach buildyourbutler.com right now", out)
        self.assertIn("Bento is ready.", out)
        self.assertNotIn("Traceback", out + proc.stderr)

    def test_skip_email_then_finish(self):
        h = self.home()
        proc = h.first_run(["", "", "", "skip", *LESSONS])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Sign in later with: butler setup", proc.stdout)
        self.assertEqual(h.secrets(), {})
        self.assertEqual(self.api.requests, {})

    def test_a_wrong_paste_is_explained(self):
        h = self.home()
        proc = h.first_run(["", "", "", "skip", "rm -rf ~", 'butler adults Sam', *LESSONS])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertGreaterEqual(proc.stdout.count("Paste the lesson's line, starting with: butler town"), 2)
        self.assertTrue(h.home.exists())

    def test_bad_email_then_good(self):
        h = self.home()
        proc = h.first_run(["", "", "", "not-an-email", "sam@example.com", "", *LESSONS])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("doesn't look like an email address", proc.stdout)
        self.assertIn("You're signed in as sam@example.com.", proc.stdout)

    def test_sign_in_down_carries_on_without_a_retry_loop(self):
        api = FakeAccountAPI(unavailable=True).start()
        self.addCleanup(api.stop)
        h = self.home(api=api)
        proc = h.first_run(["", "", "", "sam@example.com", *LESSONS])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Sign-in isn't available right now. That's on our side, not yours.", proc.stdout)
        self.assertEqual(proc.stdout.count("Your email \u203a"), 1, "it asked for the email only once")
        self.assertIn("Bento is ready.", proc.stdout)
        self.assertIn("Your brief is ready", proc.stdout)
        self.assertEqual(h.secrets(), {})

    def test_a_screen_reader_hears_words_only(self):
        h = self.home()
        h.env["CLAUDE_AX_SCREEN_READER"] = "1"
        h.env["BUTLER_BOOTSTRAP"] = "sleep 2; " + h.env["BUTLER_BOOTSTRAP"]   # still installing at the end
        proc = h.first_run(["", "", "", "skip", *LESSONS])
        out = proc.stdout
        self.assertEqual(proc.returncode, 0, out + proc.stderr)
        self.assertIn("Step 1 of 4: Your email", out)
        self.assertIn("Getting Bento ready. This can take a few minutes.", out)
        self.assertIn("Bento is ready.", out)
        self.assertNotRegex(out, "[✓·…\x1b]")
        self.assertNotRegex(out, r"ready\.\.")

    def test_a_second_run_skips_what_is_done_and_never_asks_the_name_or_email_when_signed_in(self):
        h = self.home()
        first = h.first_run(["", "", "", "sam@example.com", "", *LESSONS])
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertTrue((h.root / "data" / "first-run-started").exists())
        self.assertTrue((h.root / "data" / "first-run-done").exists())
        again = h.first_run([""])                          # Enter: not now, at Claude Code
        out = again.stdout
        self.assertEqual(again.returncode, 0, out + again.stderr)
        self.assertIn("Hello again! It's Bento, your butler.", out)
        self.assertIn("Let's pick up where we left off.", out)
        self.assertIn("Already done: you're signed in as sam@example.com.", out)
        for done in ("I know where you live.", "I know who I work for.", "your first brief is written."):
            self.assertIn(f"Already done: {done}", out)
        for asked in ("Name \u203a", "Your email \u203a"):
            self.assertNotIn(asked, out)
        self.assertEqual(out.count("Paste here \u203a"), 1, "only the optional Claude Code line")
        self.assertEqual(len([c for c in self.api.calls if c[1] == "/api/auth/start"]), 1)
        self.assertIn("/api/me", [c[1] for c in self.api.calls])
        self.assertEqual(h.settings()["household"]["adults"][0]["name"], "Sam")

    def test_an_ended_session_asks_for_the_email_again(self):
        h = self.home()
        h.keychain.write_text(json.dumps({"butler-account": "an-old-session"}))
        proc = h.first_run(["", "", "", "skip"])
        self.assertIn("Hello! I'm Bento, your new butler.", proc.stdout)
        self.assertIn("Your email \u203a", proc.stdout)

    def test_a_wrong_town_can_be_pasted_again(self):
        """Module 0's own example: "Paris" is the one in France; pasting it again fixes it (#207, #208)."""
        h = self.home()
        proc = h.first_run(["", "", "", "skip", 'butler town "Paris"', 'butler town "Paris, Texas"', "",
                            *LESSONS[2:]])
        out = proc.stdout
        self.assertEqual(proc.returncode, 0, out + proc.stderr)
        self.assertIn("Got it: Paris, Île-de-France Region, France.", out)
        self.assertIn('Not the right Paris? Add the state or country, like this: butler town "Paris, Texas"', out)
        self.assertEqual(out.count("Is that right? Press Enter, or paste the line again \u203a"), 2)
        self.assertIn("Got it: Paris, Texas, United States. Your timezone is America/Chicago, and weather "
                      "comes in Fahrenheit.", out)
        s = h.settings()
        self.assertEqual((s["timezone"], s["weather"]["unit"], s["weather"]["country"]),
                         ("America/Chicago", "fahrenheit", "US"))
        self.assertIn("I work for Sam and Alex.", out, "the run went on to the next lesson")

    def test_anything_else_at_is_that_right_is_explained(self):
        h = self.home()
        proc = h.first_run(["", "", "", "skip", LESSONS[0], "butler adults Sam Alex", "", *LESSONS[2:]])
        self.assertIn("To change the town, paste a line starting with: butler town. If it's right, press Enter.",
                      proc.stdout)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_stop_before_claude(self):
        h = self.home()
        proc = h.first_run(["", "", "", "skip", *LESSONS[:4], ""])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("When you're ready, type butler resume in a new Terminal window.", proc.stdout)
        self.assertNotIn("Butler app", proc.stdout, "an install without the package has no app")

    def test_the_end_says_where_the_butler_app_is_and_leaves_the_dock_alone(self):
        h = self.home()
        apps = Path(h.env["BUTLER_SYSTEM_APPS"])
        self.make_app(apps / "Butler.app")
        proc = h.first_run(["", "", "", "skip", *LESSONS[:4], ""])
        out = proc.stdout
        self.assertEqual(proc.returncode, 0, out + proc.stderr)
        self.assertIn("To come back any time, open the Butler app in your Applications folder. Drag it to the "
                      "Dock to keep it one click away.", out)
        self.assertIn("When you're ready, open the Butler app, or type butler resume in a new Terminal window.", out)
        self.assertLess(out.index("Butler app in your Applications folder"), out.index("What's next"))
        script = (REPO / "scripts" / "first-run.sh").read_text()
        self.assertNotIn("com.apple.dock", script)
        self.assertNotIn("dockutil", script)

    def test_an_app_in_the_home_folder_is_found_too(self):
        h = self.home()
        self.make_app(h.home / "Applications" / "Butler.app")
        proc = h.first_run(["", "", "", "skip", *LESSONS[:4], ""])
        self.assertIn("To come back any time, open the Butler app: it's in the Applications folder inside your "
                      "home folder, and Spotlight finds it too.", proc.stdout)

    @staticmethod
    def make_app(path: Path) -> None:
        (path / "Contents").mkdir(parents=True)
        (path / "Contents" / "Info.plist").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0">\n<dict>\n'
            "    <key>CFBundleIdentifier</key>\n    <string>com.buildyourbutler.butler-app</string>\n</dict>\n</plist>\n")


class WelcomeBackTest(unittest.TestCase):
    """scripts/welcome-back.command: installed over an existing Butler, resume or start fresh."""

    def setUp(self):
        self.api = FakeAccountAPI(auto_verify=True).start()
        self.addCleanup(self.api.stop)
        self.h = SetupHome(self.api, butler_name="Bento")
        self.addCleanup(self.h.close)
        done = self.h.first_run(["", "", "", "skip", *LESSONS])
        assert done.returncode == 0, done.stdout + done.stderr

    def files(self) -> dict[str, bytes]:
        """The household's files: what start fresh moves, and resume must leave alone."""
        return {str(p.relative_to(self.h.root)): p.read_bytes() for p in self.h.root.rglob("*")
                if p.is_file() and p.relative_to(self.h.root).parts[0] in ("config", "data", "out", "projects", "budget")
                and ".example." not in p.name and p.name != "progress.json"}

    def test_resume_says_where_you_are_and_changes_nothing(self):
        before = self.files()
        proc = self.h.first_run([""], "--again")
        out = proc.stdout
        self.assertEqual(proc.returncode, 0, out + proc.stderr)
        self.assertIn("Welcome back.", out)
        self.assertIn("1. Resume where you left off.", out)
        self.assertIn("2. Start fresh. Your old settings go into a backup folder first, and nothing is deleted.", out)
        self.assertIn("Press Enter to resume, or type 2 to start fresh \u203a", out)
        self.assertIn("Bento is set up through Module 0, Your first two minutes.", out)
        self.assertIn("Next: Module 1, Meet Claude Code. Type: butler claude", out)
        self.assertNotIn("Paste here", out)
        self.assertEqual(self.files(), before)

    def test_start_fresh_moves_everything_to_a_dated_backup_and_deletes_nothing(self):
        before = self.files()
        proc = self.h.first_run(["2", "yes", "", "skip"], "--again")   # then stops at the first lesson
        out = proc.stdout
        self.assertIn("Nothing is deleted. Your passwords stay in Keychain.", out)
        self.assertIn("Done. Your old settings are safe in backups/start-fresh-", out)
        backups = list((self.h.root / "backups").glob("start-fresh-*"))
        self.assertEqual(len(backups), 1)
        self.assertRegex(backups[0].name, r"^start-fresh-\d{4}-\d\d-\d\d-\d{6}$")
        for path, content in before.items():
            if path in ("data/terms.json", "data/heard.json"):
                self.assertEqual((self.h.root / path).read_bytes(), content, "the student's own answers stay")
            else:
                self.assertEqual((backups[0] / path).read_bytes(), content, path)
        self.assertIn("Sam", (backups[0] / "config" / "settings.yaml").read_text())
        # Then a fresh first run: the town is asked again.
        self.assertIn("Paste here \u203a", out)
        self.assertFalse((self.h.root / "data" / "first-run-done").exists())

    def test_start_fresh_needs_a_clear_yes(self):
        before = self.files()
        proc = self.h.first_run(["2", ""], "--again")
        self.assertIn("Alright, nothing moved.", proc.stdout)
        self.assertFalse((self.h.root / "backups").exists())
        self.assertEqual(self.files(), before)


if __name__ == "__main__":
    unittest.main()
