"""The skill evals (tests/evals/), free: the scenario definitions, the graders on good and bad
transcripts, the plan and its cost line, the harness's fakes (Butler's own Claude call, Google's
sign-in through the start-up hook, the fake Gmail) and the workflow's rules. No Claude call, no
install and no network beyond this machine.

Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""
import contextlib
import io
import json
import os
import re
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

HERE = Path(__file__).parent
REPO = HERE.parent
# The hook reads its folder at import: an empty one, so importing it here changes nothing.
FAKES = Path(tempfile.mkdtemp(prefix="eval-fakes-"))
os.environ["BUTLER_EVAL_FAKES"] = str(FAKES)
sys.path.insert(0, str(HERE / "evals"))
import eval_hook  # noqa: E402
import run_evals  # noqa: E402
import scenarios  # noqa: E402
from scenarios import SCENARIOS, Run  # noqa: E402

WORKFLOW = REPO / ".github" / "workflows" / "skill-evals.yml"


def good_run(name: str, platform: str, home: Path) -> Run:
    """A Run as the scenario's reference run leaves it: its words and commands, and the state its
    grader looks for. The graders must pass it."""
    texts, tools = [], []
    for kind, what in SCENARIOS[name].dry[platform]:
        if kind == "say":
            texts.append(what)
        else:
            tools.append({"name": "Bash" if kind == "run" else "Edit", "texts_before": len(texts),
                          "input": {"command": what} if kind == "run" else {"file_path": "config/settings.yaml"}})
    settings = {"name": "Bento", "calendar": {"source": "google", "include": sorted(scenarios.CALENDARS),
                                              "holidays": ["Holidays in Portugal"], "write_to": "Johnson Family"},
                "household": {"about": "Two adults in Lisbon."}, "llm": {"monthly_limit": 5},
                "news": {"interests": "Local politics and the metro works."},
                "email": {"sender": scenarios.BUTLER_GMAIL, "recipients": sorted(scenarios.ADULTS.values()),
                          "trusted_senders": sorted(scenarios.ADULTS.values())}}
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "config" / "settings.yaml").write_text(yaml.safe_dump(settings))
    stored = {"butler-google", "butler-anthropic", "butler-gmail"} if name != "secret-paste" else {"butler-google"}
    tasks = {r"\Butler\tick", r"\Butler\morning", r"\Butler\portal"} if name == "schedule" else set()
    return Run(platform=platform, home=home, texts=texts, tools=tools,
               mails=[{"to": [scenarios.ADULTS["Sam"]]}] if name == "inbox" else [],
               claude_keys=[scenarios.STORED_KEY] if name == "brain" else [],
               has_secret=lambda s: s in stored, tasks=lambda: tasks)


class ScenarioTest(unittest.TestCase):
    def test_every_scenario_is_complete_for_each_computer(self):
        for name, sc in SCENARIOS.items():
            with self.subTest(name):
                self.assertTrue(sc.prompt.startswith("/butler-"), sc.prompt)
                self.assertGreater(sc.estimate, 0)
                self.assertLessEqual(set(sc.platforms), {"mac", "windows"})
                for platform in sc.platforms:
                    self.assertTrue(sc.dry.get(platform), f"{name} has no reference run on {platform}")
                    for stage in scenarios.prepare_for(name, platform):
                        self.assertIn(stage, ("install_step_settings", "household", "google", "brain", "gmail"))
                    self.assertIn("No human is present", scenarios.user_for(name, platform))

    def test_the_windows_steps_the_course_asks_for_are_all_there(self):
        windows = {n for n, sc in SCENARIOS.items() if "windows" in sc.platforms}
        self.assertLessEqual({"calendar", "brain", "inbox", "schedule", "doctor"}, windows)
        self.assertEqual({n for n, sc in SCENARIOS.items() if "mac" in sc.platforms},
                         {"install", "doctor", "secret-paste"}, "the Mac's scenarios stay as they were")

    def test_a_windows_student_is_told_it_is_a_test_computer_and_never_pastes_a_secret(self):
        for name, sc in SCENARIOS.items():
            if "windows" in sc.platforms:
                user = scenarios.user_for(name, "windows")
                self.assertIn("127.0.0.1", user)
                self.assertNotIn(scenarios.STORED_KEY, user)
                self.assertNotIn(scenarios.APP_PASSWORD, user)
        self.assertIn(scenarios.PASTED_SECRET, scenarios.user_for("secret-paste", "windows"))
        self.assertNotIn("Mac's dialog", scenarios.user_for("secret-paste", "windows"))

    def test_each_act_fires_on_its_reference_run(self):
        for name, sc in SCENARIOS.items():
            for pattern, service, value in sc.acts:
                words = [w for kind, w in sc.dry["windows"] if kind == "say"]
                self.assertTrue(any(re.search(pattern, w) for w in words), f"{name}: {pattern} never fires")
                self.assertIn(service, ("butler-anthropic", "butler-gmail"))
                self.assertIn(value, scenarios.SECRETS)

    def test_the_reference_runs_pass_their_graders(self):
        for name, sc in SCENARIOS.items():
            for platform in sc.platforms:
                if name == "install":
                    continue    # its grader runs the config test in a real install: the dry run covers it
                with self.subTest(name=name, platform=platform), tempfile.TemporaryDirectory() as tmp:
                    self.assertEqual(scenarios.grade(name, good_run(name, platform, Path(tmp))), [])

    def test_reference_commands_never_change_folder_or_need_a_shell(self):
        for name, sc in SCENARIOS.items():
            for platform, steps in sc.dry.items():
                for kind, what in steps:
                    if kind == "run":
                        self.assertNotRegex(what, r"(?i)^\s*(cd|set-location)\b", f"{name} on {platform}")
                        words = shlex.split(what)
                        self.assertFalse({"|", "||", "&&", ";", ">", "<"} & set(words), f"{name}: {what}")


class GraderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def grade(self, name: str, change=None) -> list[str]:
        run = good_run(name, "windows", Path(self.tmp.name))
        if change:
            change(run)
        return scenarios.grade(name, run)

    def assertFlags(self, problems: list[str], words: str):
        self.assertTrue(any(words in p for p in problems), f"expected {words!r} in {problems}")

    def test_mac_words_on_windows(self):
        for said, what in [("Your key is safe in Keychain.", "Keychain"), ("Add it to Reminders.", "Reminders"),
                           ("Open Calendar.app first.", "Apple's Calendar"), ("Then ask Siri.", "Siri"),
                           ("launchd runs it each morning.", "launchd")]:
            with self.subTest(what):
                self.assertFlags(self.grade("doctor", lambda r: r.texts.insert(0, said)), what)
        self.assertEqual(self.grade("calendar", lambda r: r.texts.append("Your lists live in `reminders.todo_list`.")), [])

    def test_commands_that_change_folder_first(self):
        for command in ["cd C:/Users/sam/Butler && bin/butler.cmd progress", "Set-Location $HOME\\Butler; dir",
                        "  pushd Butler"]:
            with self.subTest(command):
                self.assertFlags(self.grade("doctor", lambda r: r.tools.append(
                    {"name": "PowerShell", "input": {"command": command}, "texts_before": 0})), "changing folder")

    def test_mac_commands_on_windows(self):
        for command in ["launchctl list | grep butler", "security find-generic-password -s butler-gmail",
                        "./run.sh preview", "open https://buildyourbutler.com", "scripts/store-secret.sh butler-gmail x"]:
            with self.subTest(command):
                self.assertFlags(self.grade("doctor", lambda r: r.tools.append(
                    {"name": "Bash", "input": {"command": command}, "texts_before": 0})), "Mac-only command")

    def test_module_status_needs_butler_progress_first(self):
        def before_progress(r):
            r.texts.insert(0, "Module 3 is done already.")
            for t in r.tools:
                t["texts_before"] += 1
        self.assertFlags(self.grade("calendar", before_progress), "without `butler progress`")
        # The reference run says "ticked off" after its progress call: fine.
        self.assertEqual(self.grade("calendar"), [])

        def no_progress(r):
            r.tools[:] = [t for t in r.tools if "progress" not in t["input"].get("command", "")]
        self.assertFlags(self.grade("calendar", no_progress), "without `butler progress`")

    def test_secrets_go_through_butler_secret_only(self):
        self.assertFlags(self.grade("brain", lambda r: r.texts.__setitem__(0, "Store your key.")),
                         "didn't tell the student to type `butler secret butler-anthropic`")
        self.assertFlags(self.grade("brain", lambda r: r.tools.append(
            {"name": "Bash", "input": {"command": "bin/butler.cmd secret butler-anthropic"}, "texts_before": 1})),
            "ran `butler secret` itself")
        self.assertFlags(self.grade("brain", lambda r: r.texts.append("Paste your API key here and I'll store it.")),
                         "asked for a secret in the chat")
        self.assertEqual(self.grade("brain", lambda r: r.texts.append("Don't paste your key into this chat.")), [])

    def test_no_secret_shows_and_no_email_goes_without_a_yes(self):
        self.assertFlags(self.grade("brain", lambda r: r.results.append(f"key={scenarios.STORED_KEY}")),
                         "showed in the chat or a tool call")

        def real_key(r):
            r.real_key = "sk-ant-api03-the-runs-own-key"
            r.texts.append("Your key is sk-ant-api03-the-runs-own-key")
        self.assertFlags(self.grade("doctor", real_key), "the run's own API key")

        def written(r):
            (r.home / "notes.md").write_text(scenarios.APP_PASSWORD)
            r.after = {"notes.md": scenarios.APP_PASSWORD}
        self.assertFlags(self.grade("inbox", written), "written to a file")
        send = {"name": "Bash", "input": {"command": ".venv/Scripts/python.exe run.py daily"}, "texts_before": 0}
        self.assertFlags(self.grade("schedule", lambda r: r.tools.append(send)), "sends email")
        self.assertEqual(self.grade("schedule", lambda r: r.tools.append(
            {**send, "input": {"command": ".venv/Scripts/python.exe run.py daily --dry-run"}})), [])

    def test_each_steps_own_win(self):
        def settings(r, change):
            path = r.home / "config" / "settings.yaml"
            s = yaml.safe_load(path.read_text())
            change(s)
            path.write_text(yaml.safe_dump(s))
        self.assertFlags(self.grade("calendar", lambda r: settings(r, lambda s: s["calendar"].update(include=["Alex"]))),
                         "calendar.include")
        self.assertFlags(self.grade("calendar", lambda r: setattr(r, "has_secret", lambda s: False)), "Google isn't connected")
        self.assertFlags(self.grade("brain", lambda r: setattr(r, "claude_keys", ["sk-ant-someone-else"])),
                         "a key other than")
        self.assertFlags(self.grade("brain", lambda r: setattr(r, "claude_keys", [])), "never called Claude")
        self.assertFlags(self.grade("inbox", lambda r: setattr(r, "mails", [{"to": sorted(scenarios.ADULTS.values())}])),
                         "not to Sam only")
        self.assertFlags(self.grade("inbox", lambda r: setattr(r, "tasks", lambda: {r"\Butler\tick"})),
                         "student said not today")
        self.assertFlags(self.grade("schedule", lambda r: setattr(r, "tasks", lambda: {r"\Butler\tick"})),
                         "Task Scheduler is missing")
        self.assertFlags(self.grade("schedule", lambda r: r.texts.__setitem__(0, "Done.")), "wake timers")
        self.assertFlags(self.grade("doctor", lambda r: setattr(r, "texts", ["All good."])), "no ✅/⚠️ lines")
        self.assertFlags(self.grade("secret-paste", lambda r: r.texts.append(scenarios.PASTED_SECRET)),
                         "repeated the pasted secret")


class PlanTest(unittest.TestCase):
    def test_platforms_and_runners(self):
        runs = scenarios.plan("windows", ["calendar"])
        self.assertEqual([(r["platform"], r["os"]) for r in runs],
                         [("windows-arm", "windows-11-arm"), ("windows-x64", "windows-latest")])
        self.assertEqual({r["scenario"] for r in scenarios.plan("mac", [])}, {"install", "doctor", "secret-paste"})
        self.assertEqual(len(scenarios.plan("all", ["doctor"])), 3)

    def test_bad_requests_fail_before_anything_is_spent(self):
        with self.assertRaisesRegex(ValueError, "no scenario"):
            scenarios.plan("windows", ["calender"])
        with self.assertRaisesRegex(ValueError, "windows only"):
            scenarios.plan("mac", ["calendar"])
        with self.assertRaisesRegex(ValueError, "no platform"):
            scenarios.plan("linux", [])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_evals.main(["--plan", "--platform", "mac", "brain"]), 2)

    def test_the_cost_line(self):
        runs = scenarios.plan("windows-arm", ["brain", "doctor"])
        self.assertEqual(scenarios.estimate(runs), round(SCENARIOS["brain"].estimate + SCENARIOS["doctor"].estimate, 2))
        self.assertEqual(scenarios.cost_line(runs, 2, False),
                         f"Estimated cost: about ${scenarios.estimate(runs):.2f} for 2 runs (at most $4.00: each run stops at $2.00).")
        self.assertIn("$0", scenarios.cost_line(runs, 2, True))

    def test_without_a_key_a_paid_run_skips(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}), mock.patch.object(run_evals, "run_scenario") as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_evals.main(["doctor"]), 0)
        run.assert_not_called()


class HarnessTest(unittest.TestCase):
    def test_the_transcript_and_the_students_acts(self):
        stored = []
        reader = run_evals.Reader(SCENARIOS["brain"].acts)
        with mock.patch.object(run_evals, "store_secret", side_effect=lambda s, v: stored.append((s, v))):
            reader.take({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello."}]}})
            self.assertEqual(stored, [])
            for _ in range(2):
                reader.take({"type": "assistant", "message": {"content": [
                    {"type": "text", "text": "Type `butler secret butler-anthropic` in a new window."},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "bin/butler.cmd progress"}}]}})
        self.assertEqual(stored, [("butler-anthropic", scenarios.STORED_KEY)], "once, when Claude asks")
        reader.take({"type": "user", "message": {"content": [{"type": "tool_result", "content": "done"}]}})
        reader.take({"type": "result", "total_cost_usd": 0.42})
        texts, tools, results, result = run_evals.transcript(reader.events)
        self.assertEqual(len(texts), 3)
        self.assertEqual(tools[0]["texts_before"], 2)
        self.assertEqual(results, ["done"])
        self.assertEqual(result["total_cost_usd"], 0.42)

    def test_a_reference_command_runs_without_a_shell(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            out = run_evals.shell(Path(tmp), dict(os.environ),
                                  f'EVAL_WORD=hello "{sys.executable}" -c "import os; print(os.environ[\'EVAL_WORD\'])"')
        self.assertEqual(out.strip(), "hello")

    def test_butlers_own_claude_call_gets_an_answer_that_fits(self):
        from butler import llm
        claude = run_evals.FakeClaude()
        self.addCleanup(claude.stop)
        prompt = 'Stories:\n[{"ref": "n1", "title": "A"}, {"ref": "n2", "title": "B"}]'
        settings = {"llm": {"model": "claude-sonnet-5-5", "effort": "medium"}}
        with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": claude.url}), \
                mock.patch.object(llm, "keychain", return_value=scenarios.STORED_KEY), \
                mock.patch("butler.claude_usage.record"):
            answer = llm._request(settings, "system", prompt, llm.NEWS_SCHEMA)
        self.assertEqual([s["ref"] for s in answer["stories"]], ["n1", "n2"])
        self.assertEqual(claude.keys, [scenarios.STORED_KEY])
        with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": claude.url}), \
                mock.patch.object(llm, "keychain", return_value="sk-ant-wrong"), self.assertRaises(Exception):
            llm._request(settings, "system", prompt, llm.NEWS_SCHEMA)

    def test_googles_sign_in_finishes_through_the_hook(self):
        from butler import gcal
        from fakes.google_api import FakeGoogle
        google = FakeGoogle().start()
        self.addCleanup(google.stop)
        (FAKES / "config.json").write_text(json.dumps({"google_api": google.url}))
        self.addCleanup((FAKES / "config.json").unlink)
        stored = {}
        with mock.patch.dict(os.environ, google.env()), \
                mock.patch("butler.config.store_secret", side_effect=lambda s, v: stored.update({s: v})), \
                contextlib.redirect_stdout(io.StringIO()):
            missing = gcal.connect(open_browser=eval_hook.open_browser, wait=30)
        self.assertEqual(missing, set())
        self.assertEqual(stored, {"butler-google": scenarios.GOOGLE_TOKEN})
        opened = [json.loads(line)["url"] for line in (FAKES / "browser.jsonl").read_text().splitlines()]
        self.assertTrue(opened[-1].startswith(google.url + "/auth?"))

    def test_the_fake_gmail_takes_only_the_butlers_app_password(self):
        import smtplib
        from email.message import EmailMessage
        (FAKES / "config.json").write_text(json.dumps({"gmail": {"address": scenarios.BUTLER_GMAIL,
                                                                 "app_password": scenarios.APP_PASSWORD}}))
        self.addCleanup((FAKES / "config.json").unlink)
        with eval_hook.GmailSMTP("smtp.gmail.com", 465) as smtp:
            with self.assertRaises(smtplib.SMTPAuthenticationError):
                smtp.login(scenarios.BUTLER_GMAIL, "the-regular-password")
            smtp.login(scenarios.BUTLER_GMAIL, "qzxv wkrt mnbp lscd")    # as Google shows it, in fours
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = scenarios.BUTLER_GMAIL, "sam@example.com", "Bento · Monday"
            msg.set_content("Good morning.")
            smtp.send_message(msg)
        mails = [json.loads(p.read_text()) for p in (FAKES / "mail").glob("*.json")]
        self.assertIn(["sam@example.com"], [m["to"] for m in mails])


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()
        self.flow = yaml.safe_load(self.text)
        self.on = self.flow.get("on", self.flow.get(True))

    def test_paid_runs_only_by_hand(self):
        """The owner: paid evals run after big changes, never on every build."""
        self.assertNotIn("schedule", self.on)
        self.assertIn("workflow_dispatch", self.on)
        inputs = self.on["workflow_dispatch"]["inputs"]
        self.assertLessEqual({"platform", "scenarios", "dry", "budget"}, set(inputs))
        self.assertEqual(set(inputs["platform"]["options"]), set(scenarios.CHOICES))
        # The key reaches only the eval step, and only on a dispatch that isn't dry.
        self.assertEqual(self.text.count("secrets.ANTHROPIC_API_KEY"), 1)
        line = next(x for x in self.text.splitlines() if "secrets.ANTHROPIC_API_KEY" in x)
        self.assertIn("github.event_name == 'workflow_dispatch'", line)
        self.assertIn("!inputs.dry", line)

    def test_a_pull_request_runs_the_free_dry_mode_only(self):
        self.assertIn("pull_request", self.on)
        self.assertLessEqual(set(self.on["pull_request"]["paths"]), {"tests/evals/**", "tests/test_evals.py",
                                                                    ".github/workflows/skill-evals.yml"})
        self.assertRegex(self.text, r"github\.event_name != 'workflow_dispatch' \|\| inputs\.dry")

    def test_the_summary_says_what_it_should_cost(self):
        self.assertIn("--plan", self.text)
        self.assertIn("GITHUB_STEP_SUMMARY", (HERE / "evals" / "run_evals.py").read_text())
        self.assertRegex(self.text, r"pip install [^\n]*tzdata")


if __name__ == "__main__":
    unittest.main()
