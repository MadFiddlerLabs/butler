"""The guided first run in Python (src/butler/first_run.py), scripts/first-run.sh's twin for
Windows. Its flows run on every platform, Windows included. Where bash exists, EquivalenceTest
gives both first runs the same answers and checks they print, write, keep and send the same.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from butler import account, first_run, system

from fakes.account_api import FakeAccountAPI
from fakes.setup_home import SetupHome, WINDOWS

REPO = Path(__file__).resolve().parents[1]
# The town, Enter at "Is that right?", the adults, the brief, then Claude Code (Enter installs it).
LESSONS = ['butler town "Lisbon, Portugal"', "", "butler adults Sam Alex", "butler brief", "butler claude", ""]
COMPUTER = first_run.COMPUTER[system.NAME]


def delayed(command: str) -> str:
    """A bootstrap that takes two seconds, so it's still running when the sign-in is done."""
    wait = f'"{sys.executable}" -c "import time; time.sleep(2)"'
    return f"{wait} && {command}" if WINDOWS else f"{wait}; {command}"


class FirstRunFlowTest(unittest.TestCase):
    """first_run.py end to end, with scripted keyboard input, on this platform. Most runs end at
    the first lesson, when the answers run out (exit 1): the whole run is one test."""

    def setUp(self):
        self.api = FakeAccountAPI(auto_verify=True).start()
        self.addCleanup(self.api.stop)

    def home(self, api="default", name="Bento") -> SetupHome:
        h = SetupHome(self.api if api == "default" else api, butler_name=name)
        self.addCleanup(h.close)
        return h

    def assert_on_path(self, h: SetupHome) -> None:
        """`butler` works from any new Terminal window."""
        if WINDOWS:
            self.assertEqual(Path(h.env["BUTLER_FAKE_PATH"]).read_text().splitlines(),
                             [str((h.root / "bin").resolve())])
        else:
            self.assertIn(f'export PATH="{h.root.resolve()}/bin:$PATH"', (h.home / ".zprofile").read_text())

    def test_the_whole_first_run(self):
        h = self.home()
        proc = h.py_first_run(["", "", "3", "sam@example.com", "yes", *LESSONS])
        out = proc.stdout
        self.assertEqual(proc.returncode, 0, out + proc.stderr)
        self.assertIn("Hello! I'm Bento, your new butler.", out)
        self.assertIn(f"We never see your household's data. It goes only from this {COMPUTER} to services you "
                      "choose, such as Anthropic and Google.", out)
        self.assertIn("You're signed in as sam@example.com.", out)
        self.assertIn("Bento is ready.", out)
        self.assertIn("I work for Sam and Alex.", out)
        self.assertIn("Your brief is ready", out)
        self.assertIn("It starts with Module 1: Meet Claude Code.", out)
        self.assertEqual(self.api.prefs, [{"email": "sam@example.com", "nudges": True}])
        # The session went to the password store, never to a file.
        token = h.secrets().get("butler-account")
        self.assertIn(token, self.api.issued)
        for f in h.root.rglob("*"):
            if f.is_file() and f.suffix in {".yaml", ".txt", ".log", ".md", ".json", ""}:
                self.assertNotIn(token, f.read_text(errors="replace"), str(f))
        s = h.settings()
        self.assertEqual((s["name"], s["timezone"]), ("Bento", "Europe/Lisbon"))
        request = self.api.requests[next(iter(self.api.requests))]
        terms = json.loads((h.root / "data" / "terms.json").read_text())
        self.assertEqual(terms["terms_version"], (h.root / "TERMS_VERSION").read_text().strip())
        self.assertEqual((request["butler_name"], request["terms_accepted_at"], request["heard"]),
                         ("Bento", terms["terms_accepted_at"], "reddit"))
        self.assertEqual(json.loads((h.root / "data" / "heard.json").read_text()), {"heard": "reddit"})
        lessons = [p["lesson"] for p in self.api.progress]
        for lesson in ("setup.email", "setup.name", "setup.town", "setup.first-brief", "setup.claude"):
            self.assertIn(lesson, lessons)
        for prompt in ("Name › ", "Press Enter to agree, or type no to stop › ",
                       "Type a number, or press Enter to skip › ", "Your email › ", "Paste here › "):
            self.assertIn(prompt, out)
        # bash writes \n line ends, and so does the Python first run, Windows included.
        for name in (".butler-name", "data/terms.json", "data/heard.json", "data/first-run-done"):
            self.assertNotIn(b"\r", (h.root / name).read_bytes(), name)
        self.assertIn("Lovely. I'm Bento from now on.", (h.root / "logs" / "setup.log").read_text())
        self.assert_on_path(h)
        self.assertNotIn("Traceback", out + proc.stderr)

    def test_declining_the_terms_exits_kindly(self):
        h = self.home()
        proc = h.py_first_run(["", "n"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Nothing has been sent anywhere", proc.stdout)
        self.assertEqual(self.api.calls, [])
        self.assertFalse((h.root / "data" / "terms.json").exists())
        self.assertFalse((h.root / "config" / "settings.yaml").exists())
        self.assert_on_path(h)

    def test_offline_carries_on_to_the_first_lesson(self):
        h = self.home(api=None)
        proc = h.py_first_run(["", "", "", "sam@example.com"])
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("I can't reach buildyourbutler.com right now", proc.stdout)
        self.assertIn("Bento is ready.", proc.stdout)
        self.assertIn("sign-in: couldn't reach buildyourbutler.com", (h.root / "logs" / "setup.log").read_text())
        self.assertNotIn("Traceback", proc.stdout + proc.stderr)

    def test_a_second_run_skips_what_is_done(self):
        h = self.home()
        h.lessons_done()
        first = h.py_first_run(["", "", "", "sam@example.com", "", ""])
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertTrue((h.root / "data" / "first-run-done").exists())
        again = h.py_first_run([""])
        out = again.stdout
        self.assertEqual(again.returncode, 0, out + again.stderr)
        self.assertIn("Hello again! It's Bento, your butler.", out)
        self.assertIn("Let's pick up where we left off.", out)
        self.assertIn("Already done: you're signed in as sam@example.com.", out)
        for done in ("I know where you live.", "I know who I work for.", "your first brief is written."):
            self.assertIn(f"Already done: {done}", out)
        self.assertEqual(out.count("Paste here \u203a"), 1, "only the optional Claude Code line")
        self.assertIn("click Start, type Butler and press Enter, or type butler resume in a new Terminal window." if WINDOWS
                      else "When you're ready, type butler resume in a new Terminal window.", out)
        self.assertNotIn("I added", out, "the PATH has it already")

    def test_an_ended_session_asks_for_the_email_again(self):
        h = self.home()
        h.keychain.write_text(json.dumps({"butler-account": "an-old-session"}))
        proc = h.py_first_run(["", "", "", "skip"])
        self.assertEqual(proc.returncode, 1, "the answers ran out at the first lesson")
        self.assertIn("Hello! I'm Bento, your new butler.", proc.stdout)
        self.assertIn("Your email \u203a", proc.stdout)
        self.assertIn("No problem. Sign in later with: butler setup", proc.stdout)

    def test_no_name_from_the_download_asks_for_one(self):
        h = self.home(name=None)
        proc = h.py_first_run(["Bad!Name", "Alfred", "", "", "skip"])
        self.assertIn("That one won't work.", proc.stdout)
        self.assertEqual((h.root / ".butler-name").read_bytes(), b"Alfred\n")
        self.assertEqual(self.api.calls, [], "skipped the email")

    def test_a_screen_reader_hears_words_only(self):
        h = self.home()
        h.env["CLAUDE_AX_SCREEN_READER"] = "1"
        h.env["BUTLER_BOOTSTRAP"] = delayed(h.env["BUTLER_BOOTSTRAP"])
        proc = h.py_first_run(["", "", "", "skip"])
        out = proc.stdout
        self.assertIn("Step 1 of 4: Your email", out)
        self.assertIn("Getting Bento ready. This can take a few minutes.", out)
        self.assertIn("Bento is ready.", out)
        self.assertNotRegex(out, "[✓·…\x1b]")

    def test_skip_while_waiting_for_the_click(self):
        api = FakeAccountAPI().start()
        self.addCleanup(api.stop)
        h = self.home(api=api)
        proc = h.py_first_run(["", "", "", "sam@example.com", "skip"])
        self.assertIn("Waiting for your click.", proc.stdout)
        self.assertIn("We'll finish signing in later. Type: butler setup", proc.stdout)
        self.assertEqual([c[1] for c in api.calls].count("/api/auth/poll"), 1, "skip stopped the polling")

    def test_a_click_while_waiting_signs_in(self):
        """A student who types nothing while the email arrives: the polls wait on the keyboard,
        and what they type after the click goes to the next question."""
        api = FakeAccountAPI().start()
        self.addCleanup(api.stop)
        h = self.home(api=api)
        h.env["BUTLER_POLL_SECONDS"] = "0.2"
        proc = subprocess.Popen([sys.executable, "-X", "utf8", str(h.root / "run.py"), "setup"], cwd=h.home,
                                env=h.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, encoding="utf-8")
        printed: list[str] = []
        reader = threading.Thread(target=lambda: printed.extend(iter(lambda: proc.stdout.read(1), "")), daemon=True)
        reader.start()

        def until(condition) -> None:
            for _ in range(600):
                if condition():
                    return
                threading.Event().wait(0.1)
            proc.kill()
            self.fail("".join(printed))

        proc.stdin.write("\n\n\nsam@example.com\n")
        proc.stdin.flush()
        until(lambda: [c[1] for c in api.calls].count("/api/auth/poll") >= 3)
        api.click()
        until(lambda: "Weekly lesson email?" in "".join(printed))
        proc.stdin.write("yes\n")
        proc.stdin.close()
        self.assertEqual(proc.wait(timeout=300), 1, "the answers ran out at the first lesson")
        reader.join(10)
        proc.stdout.close()
        out = "".join(printed)
        self.assertIn("You're signed in as sam@example.com.", out)
        self.assertEqual(api.prefs, [{"email": "sam@example.com", "nudges": True}])

    def test_a_mistyped_email_can_be_retyped_while_it_waits(self):
        api = FakeAccountAPI().start()
        self.addCleanup(api.stop)
        h = self.home(api=api)
        proc = h.py_first_run(["", "", "", "sam@exmaple.com", "butler setup", "sam@example.com", "skip"])
        self.assertIn("Sent to sam@exmaple.com!", proc.stdout)
        self.assertIn("Type your email again if it was wrong, or type skip to do this later.", proc.stdout)
        self.assertIn("Sending a new link to sam@example.com.", proc.stdout)
        self.assertEqual([r["email"] for r in api.requests.values()], ["sam@exmaple.com", "sam@example.com"])
        self.assertEqual(proc.stdout.count("Your email \u203a"), 1, "the new address isn't asked for twice")

    def test_an_expired_link_sends_a_new_one(self):
        api = FakeAccountAPI(expire=True).start()
        self.addCleanup(api.stop)
        h = self.home(api=api)
        proc = h.py_first_run(["", "", "", "sam@example.com", "skip"])
        self.assertIn("That link expired. Let's send a new one.", proc.stdout)
        self.assertEqual(proc.stdout.count("Your email \u203a"), 2)

    def test_sign_in_down_carries_on(self):
        api = FakeAccountAPI(unavailable=True).start()
        self.addCleanup(api.stop)
        h = self.home(api=api)
        proc = h.py_first_run(["", "", "", "not-an-email", "sam@example.com"])
        self.assertIn("doesn't look like an email address", proc.stdout)
        self.assertIn("Sign-in isn't available right now. That's on our side, not yours.", proc.stdout)
        self.assertEqual(proc.stdout.count("Your email \u203a"), 2)
        self.assertEqual(h.secrets(), {})

    def test_a_wrong_paste_is_explained_and_never_run(self):
        h = self.home()
        proc = h.py_first_run(["", "", "", "skip", "rm -rf ~", "butler adults Sam", 'butler town "Nowhere'])
        self.assertEqual(proc.stdout.count("Paste the lesson's line, starting with: butler town"), 2)
        self.assertTrue(h.home.exists())
        # A quote left open ends the line there, as xargs does in first-run.sh: `butler town` alone.
        self.assertIn('Tell me your town, like this: butler town "Lisbon"', proc.stdout)
        self.assertIn("Let's try that again.", proc.stdout)


class WelcomeBackFlowTest(unittest.TestCase):
    """first_run.py --again: installed over an existing Butler, resume or start fresh."""

    def setUp(self):
        self.api = FakeAccountAPI(auto_verify=True).start()
        self.addCleanup(self.api.stop)
        self.h = SetupHome(self.api, butler_name="Bento")
        self.addCleanup(self.h.close)
        self.h.lessons_done()
        done = self.h.py_first_run(["", "", "", "skip", ""])
        assert done.returncode == 0, done.stdout + done.stderr

    def files(self) -> dict[str, bytes]:
        return {p.relative_to(self.h.root).as_posix(): p.read_bytes() for p in self.h.root.rglob("*")
                if p.is_file() and p.relative_to(self.h.root).parts[0] in ("config", "data", "out", "projects", "budget")
                and ".example." not in p.name and p.name != "progress.json"}

    def test_resume_then_a_change_of_mind_then_start_fresh(self):
        before = self.files()
        proc = self.h.py_first_run([""], "--again")
        out = proc.stdout
        self.assertEqual(proc.returncode, 0, out + proc.stderr)
        self.assertIn(f"Butler was already on this {COMPUTER}.", out)
        self.assertIn("Bento is set up through Module 0, Your first two minutes.", out)
        self.assertNotIn("Paste here", out)
        self.assertEqual(self.files(), before)
        self.assertIn("welcome back", (self.h.root / "logs" / "setup.log").read_text())

        proc = self.h.py_first_run(["3", "2", ""], "--again")
        self.assertIn("Type 1 or 2, or press Enter to resume.", proc.stdout)
        self.assertIn("Alright, nothing moved.", proc.stdout)
        self.assertFalse((self.h.root / "backups").exists())
        self.assertEqual(self.files(), before)

        proc = self.h.py_first_run(["2", "yes", "", "skip"], "--again")
        out = proc.stdout
        self.assertIn(f"Nothing is deleted. Your passwords stay in {system.PASSWORD_STORE}.", out)
        backups = list((self.h.root / "backups").glob("start-fresh-*"))
        self.assertEqual(len(backups), 1)
        for path, content in before.items():
            if path in ("data/terms.json", "data/heard.json"):
                self.assertEqual((self.h.root / path).read_bytes(), content, "the student's own answers stay")
            else:
                self.assertEqual((backups[0] / path).read_bytes(), content, path)
        self.assertIn("Let's get me settled in.", out, "a fresh start isn't a resume")
        self.assertIn("Paste here \u203a", out, "the town is asked again")
        self.assertRegex((self.h.root / "logs" / "setup.log").read_text(),
                         r"start fresh: moved config/settings\.yaml .* to backups/start-fresh-")


TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?")
STAMP = re.compile(r"start-fresh-\d{4}-\d\d-\d\d-\d{6}")
ESCAPE = re.compile(r"\x1b(?:\[[0-9;?]*[A-Za-z]|[()][0-9A-Za-z])")
# Whether Python is still installing when the sign-in ends depends on timing, not on the answers.
GETTING_READY = re.compile(r"^Getting \S.* ready\. This can take a few minutes\.\n", re.M)


def online(h: SetupHome) -> None:
    """Between runs: the internet is back."""
    h.env["BUTLER_API"] = h.env["BUTLER_LATER_API"] + "/api"
    h.env["BUTLER_GEOCODE_URL"] = h.env["BUTLER_LATER_API"] + "/geocode"


def newer_terms(h: SetupHome) -> None:
    (h.root / "TERMS_VERSION").write_text("0.2\n")


def ended_session(h: SetupHome) -> None:
    h.keychain.write_text(json.dumps({"butler-account": "an-old-session"}))


def app_in_applications(h: SetupHome) -> None:
    plist = Path(h.env["BUTLER_SYSTEM_APPS"]) / "Butler.app" / "Contents" / "Info.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0">\n<dict>\n'
                     "    <key>CFBundleIdentifier</key>\n    <string>com.buildyourbutler.butler-app</string>\n"
                     "</dict>\n</plist>\n")


def app_in_the_home_folder(h: SetupHome) -> None:
    (h.home / "Applications" / "Butler.app").mkdir(parents=True)


@unittest.skipIf(WINDOWS or not shutil.which("bash"), "the Mac's first run is bash; on Windows only first_run.py runs")
class EquivalenceTest(unittest.TestCase):
    """The same answers to scripts/first-run.sh and first_run.py, each in its own fresh install
    against its own fake account API. What they print and exit with, the files they write, the
    setup log, the password store and what the API received must match, once what always
    differs is taken out: times, the temporary folders, and the network errors each logs in its
    own words. Most runs end at a lesson when the answers run out, to keep the test quick."""

    def both(self, *runs, api: dict | None = None, offline: bool = False, name: str | None = "Bento",
             env: dict | None = None) -> None:
        """Each run is (answers, args) for one first run, or a function that changes the install
        between runs. They all happen in one install, in order."""
        seen = {}
        for runner in ("first_run", "py_first_run"):
            fake = FakeAccountAPI(**(api if api is not None else {"auto_verify": True})).start()
            self.addCleanup(fake.stop)
            h = SetupHome(None if offline else fake, butler_name=name)
            self.addCleanup(h.close)
            h.env.update(env or {}, BUTLER_LATER_API=fake.url)
            printed = []
            for run in runs:
                if callable(run):
                    run(h)
                    continue
                answers, args = run
                proc = getattr(h, runner)(answers, *args)
                self.assertNotIn("Traceback", proc.stdout + proc.stderr)
                printed.append((proc.returncode, GETTING_READY.sub("", self.normal(proc.stdout, h))))
            seen[runner] = (printed, self.files(h), self.calls(h, fake), self.secrets(h, fake))
        (bash_printed, *bash), (py_printed, *py) = seen["first_run"], seen["py_first_run"]
        self.maxDiff = None
        for i, (b, p) in enumerate(zip(bash_printed, py_printed), 1):
            self.assertEqual(b[1], p[1], f"what run {i} printed")
            self.assertEqual(b[0], p[0], f"the exit code of run {i}")
        for what, b, p in zip(("the files", "what the account API received", "the password store"), bash, py):
            self.assertEqual(b, p, what)

    @staticmethod
    def normal(text: str, h: SetupHome) -> str:
        text = ESCAPE.sub("", text)
        for path, name in ((h.root.resolve(), "<butler>"), (h.root, "<butler>"), (h.home.resolve(), "<home>"),
                           (h.home, "<home>"), (h.tmp.resolve(), "<tmp>"), (h.tmp, "<tmp>")):
            text = text.replace(str(path), name)
        return STAMP.sub("start-fresh-<stamp>", TIME.sub("<time>", text))

    def files(self, h: SetupHome) -> dict[str, str]:
        """Every file the first run and its lessons write. Of the brief and the logs Butler itself
        keeps, only their names."""
        found = {}
        for p in sorted([*h.root.rglob("*"), h.home / ".zprofile"]):
            rel = p.relative_to(h.root).as_posix() if p.is_relative_to(h.root) else "~/" + p.name
            top = rel.split("/")[0]
            if not p.is_file() or ".example." in p.name or top not in (".butler-name", "config", "data", "out",
                                                                       "logs", "backups", "~"):
                continue
            key = self.normal(rel, h)
            parts = rel.split("/")       # out/ and logs/ moved to backups/ by a fresh start too
            if "out" in parts or ("logs" in parts and p.name != "setup.log"):
                found[key] = "(kept)"
                continue
            text = self.normal(p.read_text(errors="replace"), h)
            if p.name == "setup.log":
                # Each logs a network error in its own words: curl's, or the account module's.
                text = "".join(line for line in text.splitlines(keepends=True)
                               if not line.startswith("curl:") and "<time> sign-in: " not in line)
            found[key] = text
        return found

    def calls(self, h: SetupHome, api: FakeAccountAPI) -> str:
        text = json.dumps(api.calls, indent=1)
        for i, secret in enumerate(api.issued):
            text = text.replace(secret, f"<issued {i}>")
        for i, request in enumerate(api.requests):
            text = text.replace(request, f"<request {i}>")
        return self.normal(text, h)

    @staticmethod
    def secrets(h: SetupHome, api: FakeAccountAPI) -> dict:
        return {k: (f"<issued {api.issued.index(v)}>" if v in api.issued else v) for k, v in h.secrets().items()}

    def test_a_whole_first_run_then_again_with_newer_terms_then_welcome_back(self):
        self.both((["", "", "3", "sam@example.com", "yes", 'butler town "Paris"', 'butler town "Paris, Texas"', "",
                    *LESSONS[2:]], ()),
                  ([""], ()), newer_terms, (["", ""], ()),
                  ([""], ("--again",)), (["3", "2", ""], ("--again",)), (["2", "yes", "", "skip"], ("--again",)))

    def test_declining_the_terms(self):
        self.both((["", "n"], ()))

    def test_skip_the_email_rename_and_paste_the_wrong_lines(self):
        self.both((["Jeeves", "", "12", "", "skip", "rm -rf ~", "butler adults Sam", 'butler town "Lisbon"', "x",
                    "", "butler brief", "butler adults Sam Alex"], ()))

    def test_no_name_from_the_download(self):
        self.both((["Bad!Name", "Alfred", "", "", "skip"], ()), name=None)

    def test_offline_then_online(self):
        self.both((["", "", "5", "sam@example.com", 'butler town "Lisbon"'], ()), online,
                  (["", "sam@example.com", "", 'butler town "Lisbon"', ""], ()), offline=True)

    def test_sign_in_down_and_a_bad_email(self):
        self.both((["", "", "", "not-an-email", "sam@example.com"], ()), api={"unavailable": True})

    def test_an_expired_link(self):
        self.both((["", "", "", "sam@example.com", "skip"], ()), api={"expire": True})

    def test_skip_while_waiting_for_the_click(self):
        self.both((["", "", "", "sam@example.com", "skip"], ()), api={})

    def test_a_mistyped_email_retyped_while_waiting(self):
        """Found by the persona tests: the address shows back, and typing the right one while it
        waits sends a new link to it; anything else typed gets a hint, not silence."""
        self.both((["", "", "", "sam@exmaple.com", "butler setup", "sam@example.com", "skip"], ()), api={})

    def test_a_screen_reader(self):
        self.both((["", "", "", "skip"], ()), env={"CLAUDE_AX_SCREEN_READER": "1"})

    def test_an_ended_session(self):
        self.both(ended_session, (["", "", "", "skip"], ()))

    def test_the_butler_app_in_applications_then_in_the_home_folder(self):
        self.both(SetupHome.lessons_done, app_in_applications, (["", "", "", "skip", ""], ()))
        self.both(SetupHome.lessons_done, app_in_the_home_folder, (["", "", "", "skip", ""], ()))


class HeardTest(unittest.TestCase):
    def test_the_labels_and_numbers_match_heard_sh(self):
        script = (REPO / "scripts" / "lib" / "heard.sh").read_text()
        labels = re.search(r"HEARD_LABELS=\((.*?)\)", script, re.S).group(1)
        self.assertEqual(tuple(re.findall(r'"([^"]+)"', labels)), first_run.HEARD_LABELS)
        self.assertEqual([first_run.heard_token(str(n)) for n in range(1, 11)], list(account.HEARD_SOURCES))
        self.assertEqual(first_run.heard_token(" 4. "), "hacker_news")
        for answer in ("0", "11", "01", "1x", "reddit", "-1", ""):
            self.assertIsNone(first_run.heard_token(answer), answer)


class KeyboardTest(unittest.TestCase):
    def test_a_line_typed_while_waiting_goes_to_the_next_question(self):
        typed = threading.Event()

        def read():
            typed.wait(5)
            return "  later  \n"

        keys = first_run.Keyboard()
        with mock.patch.object(first_run.Keyboard, "_read", staticmethod(read)), redirect_stdout(io.StringIO()):
            self.assertEqual(keys.within(0.05), (False, None))
            typed.set()
            self.assertEqual(keys.line("Your email › "), "later")
            self.assertIsNone(keys._pending)

    def test_the_end_of_the_input_while_waiting(self):
        keys = first_run.Keyboard()
        with mock.patch.object(first_run.Keyboard, "_read", staticmethod(lambda: None)):
            self.assertEqual(keys.within(1), (True, None))

    def test_a_pipe_is_read_a_byte_at_a_time_so_a_child_gets_the_rest(self):
        """What lets `butler claude`, started by the first run, read its own answer."""
        code = ("import subprocess, sys\nfrom butler.first_run import Keyboard\n"
                "print(repr(Keyboard().line('')), flush=True)\n"
                "subprocess.run([sys.executable, '-c', 'print(repr(input()))'])\n"
                "print(repr(Keyboard().line('')))\n")
        env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
        out = subprocess.run([sys.executable, "-X", "utf8", "-c", code], input="Sam é\r\nchild\n", env=env,
                             capture_output=True, text=True, encoding="utf-8").stdout
        self.assertEqual(out.splitlines(), ["'Sam é'", "'child'", "None"])


class PiecesTest(unittest.TestCase):
    def tmp(self) -> Path:
        return Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_commands_in_sentences_stay_whole_at_80_columns(self):
        with mock.patch.dict(os.environ, {"BUTLER_COLUMNS": "80"}):
            fr = first_run.FirstRun(root=self.tmp())
        text = "word " * 13 + "and then sign in later with: butler setup and type butler claude in Terminal."
        lines = fr.wrap(text, fr.width)
        self.assertTrue(all(len(line) <= 76 for line in lines), lines)
        self.assertFalse([line for line in lines if line.endswith("butler")])
        self.assertEqual(" ".join(lines), text.rstrip())

    def test_nothing_to_move_is_already_a_fresh_start(self):
        root = self.tmp()
        (root / "data").mkdir()
        (root / "data" / "terms.json").write_text("{}")
        fr = first_run.FirstRun(root=root)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertTrue(fr.start_fresh())
        self.assertIn("There's nothing of yours to move yet", out.getvalue())

    def test_ctrl_c_ends_quietly(self):
        with mock.patch.object(first_run.FirstRun, "run", side_effect=KeyboardInterrupt), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(first_run.main([]), 130)

    def test_a_password_store_that_refuses_the_session_is_said_and_logged(self):
        root = self.tmp()
        (root / "logs").mkdir()
        fr = first_run.FirstRun(root=root)
        fr.keys = mock.Mock(line=mock.Mock(return_value=""))
        out = io.StringIO()
        with mock.patch.object(first_run, "store_secret", side_effect=OSError("locked")), \
             mock.patch.object(first_run.account, "send_progress"), \
             mock.patch.object(first_run.account, "set_nudges") as nudges, redirect_stdout(out):
            fr.signed_in_as("sam@example.com", "an-invented-token")
        self.assertIn(f"I couldn't save your sign-in to {system.PASSWORD_STORE}.", out.getvalue())
        nudges.assert_called_once_with("an-invented-token", False)
        log = (root / "logs" / "setup.log").read_text()
        self.assertIn("couldn't save the session (OSError)", log)
        self.assertNotIn("an-invented-token", log + out.getvalue().replace("signed in as", ""))

    def test_on_windows_the_butler_command_is_run_py_and_python_comes_from_the_installer(self):
        root = self.tmp()
        env = {k: v for k, v in os.environ.items() if k != "BUTLER_BOOTSTRAP"}
        with mock.patch.object(first_run.system, "NAME", "windows"), mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(first_run.subprocess, "run") as run, \
             mock.patch.object(first_run.subprocess, "Popen") as popen:
            fr = first_run.FirstRun(root=root)
            run.return_value.returncode = 0
            self.assertEqual(fr.butler("town", "Lisbon"), 0)
            fr.start_bootstrap()
            self.assertEqual(first_run.venv_python(root), root / ".venv" / "Scripts" / "python.exe")
        self.assertEqual(run.call_args.args[0], [str(root / ".venv" / "Scripts" / "python.exe"), "-X", "utf8",
                                                 str(root / "run.py"), "cli", "town", "Lisbon"])
        popen.assert_not_called()
        self.assertIsNone(fr.bootstrap)
        self.assertEqual(fr.computer, "computer")
        self.assertEqual(fr.green, "", "no colour codes in a Windows console")

    def test_on_windows_the_way_back_is_the_start_menu(self):
        """The lessons send Windows students to Start, type Butler, as the Mac's go to the Butler app."""
        with mock.patch.object(first_run.system, "NAME", "windows"):
            come_back, goodbye = first_run.way_back(None)
        self.assertEqual(come_back, "To come back any time, click Start, type Butler and press Enter.")
        self.assertIn("click Start, type Butler and press Enter, or type butler resume in a new Terminal window.", goodbye)
        installer = (REPO / "packaging" / "windows" / "butler.iss").read_text()
        self.assertIn('Name: "{userprograms}\\Butler"', installer, "the Start menu has Butler")


if __name__ == "__main__":
    unittest.main()
