"""The persona tests' free parts (tests/personas): the personas themselves, the line the harness
reads to open Claude Code, the terminal text and the report. The runs themselves are paid and CI
only (.github/workflows/personas.yml).

Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""
import os
import re
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

import yaml

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "personas"))
import report  # noqa: E402
import screens  # noqa: E402
from terminal import Screen, Terminal, plain  # noqa: E402


def cli_prompt() -> str:
    """Butler's first opening prompt for Claude Code, as open_claude passes it."""
    sys.path.insert(0, str(HERE.parent / "src"))
    from butler import cli
    return cli.FIRST_CLAUDE_PROMPT.format(manners=cli.MANNERS["auto"])


class PersonasTest(unittest.TestCase):
    def test_each_persona_is_complete_and_invented(self):
        spec = yaml.safe_load((HERE / "personas" / "personas.yaml").read_text())
        self.assertGreaterEqual(len(spec["personas"]), 3)
        for key, p in spec["personas"].items():
            with self.subTest(key):
                self.assertTrue(p["name"] and len(p["who"]) > 200)
                self.assertGreaterEqual(len(p["deck"]), spec["draw"] * 2, "enough cards that runs differ")
                self.assertEqual(len(set(p["deck"])), len(p["deck"]))

    def test_the_harness_sees_butler_open_claude_code_as_cli_says_it(self):
        """The harness cuts the window where Butler says Claude Code is opening (cli.open_claude),
        then takes that Claude Code over. Change both together."""
        cli = (HERE.parent / "src" / "butler" / "cli.py").read_text()
        for line in ('say("Opening Claude Code in your Butler folder. If it asks you to sign in, follow the link it shows.")',
                     'say(f"{opening} If it asks you to sign in, follow the link it shows.")',
                     'say("It starts with Module 1: Meet Claude Code." if first else "It picks up where you left off in the course.")'):
            self.assertIn(line, cli)
        screen = ("Type 1 or 2, or press Enter for Ask first: \nAsk first it is.\nOpening Claude Code in your Butler folder. "
                  "If it asks you to sign in, follow the link it shows.\nIt starts with Module 1: Meet Claude Code.\n"
                  "╭─ Welcome to Claude Code ─╮\n")
        m = screens.CLAUDE_OPENING.search(screen)
        self.assertTrue(screen[m.end():].startswith("╭─ Welcome"), "everything after is Claude Code's own screen")
        resumed = "Back where you left off. If it\nasks you to sign in, follow the link it shows.\n▐▛███▜▌\n"
        self.assertTrue(resumed[screens.CLAUDE_OPENING.search(resumed).end():].startswith("▐▛"))

    def test_the_claude_code_butler_started_is_told_from_the_sdks_own(self):
        prompt = cli_prompt()
        argv = ["C:\\Users\\BYB Test\\.local\\bin\\claude.exe", "--permission-mode", "default", prompt]
        line = subprocess.list2cmdline(argv)
        self.assertEqual(screens.split_windows(line), argv)
        self.assertEqual(screens.butler_claude(screens.split_windows(line)),
                         {"exe": argv[0], "mode": "default", "prompt": prompt})
        sdk = ["C:\\Users\\BYB Test\\.local\\bin\\claude.exe", "--output-format", "stream-json", "--verbose"]
        self.assertIsNone(screens.butler_claude(screens.split_windows(subprocess.list2cmdline(sdk))))
        self.assertIsNone(screens.butler_claude(["cmd.exe", "/k", "butler.cmd", "claude"]))
        mac = f"/Users/byb test/.local/bin/claude --permission-mode acceptEdits {prompt}"
        self.assertEqual(screens.mac_claude(mac), {"exe": "/Users/byb test/.local/bin/claude", "mode": "acceptEdits",
                                                   "prompt": prompt})
        self.assertIsNone(screens.mac_claude("/Users/byb test/.local/bin/claude --output-format stream-json --verbose"))
        self.assertIsNone(screens.mac_claude("/bin/zsh -l"))

    def test_a_windows_command_line_splits_as_windows_splits_it(self):
        for argv in (["a b", 'say "hi"', "back\\slash\\", "", 'x\\"y', "C:\\Program Files\\"],
                     ["butler.cmd", "setup", "--again"], ['He said ""no""', "\\\\server\\share"]):
            with self.subTest(argv):
                self.assertEqual(screens.split_windows(subprocess.list2cmdline(argv)), argv)
        # The installer's window, as installed.console_command writes it: cmd keeps the inner line.
        self.assertEqual(screens.split_windows('cmd.exe /k ""C:\\Users\\BYB Test\\Butler\\bin\\butler.cmd" setup"')[:2],
                         ["cmd.exe", "/k"])

    def test_terminal_runs_a_command_file_the_way_terminal_types_it(self):
        self.assertEqual(screens.terminal_types("/Users/byb test/Butler/scripts/first-run.command"),
                         "/Users/byb\\ test/Butler/scripts/first-run.command ; exit;")
        self.assertEqual(screens.terminal_types("/Users/o'brien (2)/x.command"), "/Users/o\\'brien\\ \\(2\\)/x.command ; exit;")

    def test_the_harness_opens_what_the_postinstall_says_it_opened(self):
        """packaging/macos/scripts/postinstall's lines in logs/install.log; change both together."""
        post = (HERE.parent / "packaging" / "macos" / "scripts" / "postinstall").read_text()
        self.assertIn('note "installed ($state): could not open Terminal with $(basename "$file") ($how, exit $code)"', post)
        self.assertIn('note "installed ($state): opened Terminal with $(basename "$file") ($how)"', post)
        log = ("2026-10-28T10:00:00Z v0.0.1 the Butler app stays in ~/Applications: bybtest isn't an admin\n"
               "2026-10-28T10:00:01Z v0.0.1 installed (fresh): could not open Terminal with first-run.command "
               "(launchctl asuser 502 open; bybtest isn't the one at the screen, so not open, exit 5)\n")
        self.assertEqual(screens.install_log_opened(log), (False, "first-run.command"))
        self.assertEqual(screens.install_log_opened("x installed (started): opened Terminal with welcome-back.command (open)\n"),
                         (True, "welcome-back.command"))
        self.assertIsNone(screens.install_log_opened("x installed (fresh), from the command line: Terminal not opened\n"))

    def test_keys_for_a_console_window(self):
        self.assertEqual(screens.console_keys("ab\r"), [(None, "a"), (None, "b"), (0x0D, "\r")])
        self.assertEqual(screens.console_keys("\x1b[A\x1b\x03\x7f›"),
                         [(0x26, "\0"), (0x1B, "\x1b"), "ctrl_c", (0x08, "\b"), (None, "›")])

    def test_the_installers_windows_read_as_a_person_sees_them(self):
        ready = screens.window_form("Setup - Butler", [
            ("TNewStaticText", "Ready to Install", True, True), ("TNewMemo", "Destination location:\r\n  C:\\Users\\BYB Test\\Butler", True, True),
            ("TNewButton", "< &Back", True, True), ("TNewButton", "&Install", True, True), ("TNewButton", "Cancel", True, True),
            ("TNewButton", "&Next >", True, False), ("TNewCheckBox", "Create a desktop shortcut", True, True)])
        self.assertEqual(ready["buttons"], ["< Back", "Install", "Cancel"], "hidden buttons aren't shown")
        self.assertEqual(ready["boxes"], ["Create a desktop shortcut"])
        self.assertIn("Ready to Install", ready["text"])
        self.assertFalse(screens.waiting_only(ready))
        self.assertEqual(screens.forward(ready)["button"], "Install")
        busy = screens.window_form("Setup - Butler", [("TNewStaticText", "Getting Butler's own Python.", True, True),
                                                      ("TNewButton", "Cancel", True, True)])
        self.assertTrue(screens.waiting_only(busy), "a page with only Cancel is a wait, not a question")
        warning = screens.window_form("Open File - Security Warning", [
            ("Static", "Do you want to run this file?", True, True), ("Button", "&Run", True, True), ("Button", "Cancel", True, True)])
        self.assertEqual(screens.forward(warning)["button"], "Run")
        smartscreen = {"buttons": ["More info", "Don't run"]}
        self.assertEqual(screens.forward(smartscreen)["button"], "More info")
        self.assertEqual(screens.forward({"buttons": ["Run anyway", "Don't run"]})["button"], "Run anyway")

    def test_the_rehearsal_answers_the_prompts_the_first_run_shows(self):
        """run_persona.Run.PROMPTS against scripts/first-run.sh, first_run.py and cli.py: a prompt
        renamed there would leave the free rehearsal waiting."""
        source = "".join((HERE.parent / p).read_text() for p in ("scripts/first-run.sh", "src/butler/first_run.py",
                                                                  "src/butler/cli.py"))
        source = source.replace("\\u203a", "›")
        harness = (HERE / "personas" / "run_persona.py").read_text()
        prompts = re.findall(r'\("([^"]+[›:])", "', harness[harness.index("PROMPTS = ("):harness.index("async def act")])
        self.assertGreaterEqual(len(prompts), 9)
        for prompt in prompts:
            with self.subTest(prompt):
                self.assertIn(prompt, source)

    def test_launchctl_for_an_account_with_no_screen_session(self):
        """stubs/launchctl.py moves gui/<uid> to user/<uid>, and `list` shows that domain's jobs."""
        sys.path.insert(0, str(HERE / "personas" / "stubs"))
        import launchctl
        self.assertEqual(launchctl.mapped(["bootstrap", "gui/502", "/Users/byb test/Library/LaunchAgents/a.plist"], 502),
                         ["bootstrap", "user/502", "/Users/byb test/Library/LaunchAgents/a.plist"])
        self.assertEqual(launchctl.mapped(["kickstart", "-k", "gui/502/com.buildyourbutler.portal"], 502),
                         ["kickstart", "-k", "user/502/com.buildyourbutler.portal"])
        self.assertEqual(launchctl.mapped(["print", "gui/5020"], 502), ["print", "gui/5020"], "another account's domain")
        printed = ("user/502 = {\n\ttype = User\n\tservices = {\n\t\t   88008   (pe) \tcom.apple.x\n"
                   "\t\t       0      - \tcom.buildyourbutler.butler\n\t\t    4242      0 \tcom.buildyourbutler.portal\n\t}\n}\n")
        self.assertEqual(launchctl.services(printed), [("88008", "0", "com.apple.x"), ("-", "0", "com.buildyourbutler.butler"),
                                                      ("4242", "0", "com.buildyourbutler.portal")])

    def test_an_admin_step_is_done_by_the_runners_admin_when_the_run_is_a_standard_account(self):
        import admin
        import tempfile
        import threading
        done = []
        with unittest.mock.patch.dict(admin.STEPS, {"note": lambda **kw: done.append(kw)}):
            with unittest.mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("BYB_ADMIN_REQUESTS", None)
                admin.request("note", who="itself")
            self.assertEqual(done, [{"who": "itself"}], "an admin run does it itself")
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder, \
                    unittest.mock.patch.dict(os.environ, {"BYB_ADMIN_REQUESTS": folder}):  # the server thread may still write
                threading.Thread(target=admin.serve, args=(Path(folder),), daemon=True).start()
                admin.request("note", who="for the student")
                self.assertEqual(done[-1], {"who": "for the student"})
                with self.assertRaises(RuntimeError):
                    admin.request("no-such-step")

    def test_the_runs_key_is_taken_out_of_the_environment(self):
        import tempfile
        sys.path.insert(0, str(HERE / "personas"))
        source = (HERE / "personas" / "run_persona.py").read_text()
        namespace = {"os": os, "Path": Path}
        exec(source[source.index("def read_key"):source.index("def main() -> int")], namespace)
        with tempfile.TemporaryDirectory() as folder, unittest.mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "env-key"}):
            key = Path(folder) / "key"
            key.write_text("file-key\n")
            self.assertEqual(namespace["read_key"](str(key)), "file-key")
            self.assertNotIn("ANTHROPIC_API_KEY", os.environ, "no window or program the student opens inherits it")
            self.assertFalse(key.exists(), "read once, then gone")

    def test_the_name_box_works_as_the_homepage_does_and_as_butler_checks(self):
        from butler.setup_settings import valid_name
        for typed, want in [("Jeeves", "Jeeves"), ("  Mrs   Hudson ", "Mrs Hudson"), ("O'Brien", "O'Brien"),
                            ("Type Jeeves in the name box and click Download", "Type Jeeves in the name"), ("🤖 Robo", "Butler"),
                            ("", "Butler"), ("-dash", "Butler")]:
            with self.subTest(typed):
                got, said = screens.site_name(typed)
                self.assertEqual(got, want)
                if got != "Butler":
                    self.assertEqual(valid_name(got), got, "the setup accepts what the site lets through")
                self.assertEqual(bool(said), got == "Butler" and typed.strip() != "")

    def test_a_question_mid_action_is_one_the_api_takes_and_keeps_the_cache(self):
        """Claude Code's questions come while the persona's claude_code call runs. A plain question
        after a pending tool call is a 400 (every pilot answer fell back to "yes" for that)."""
        from types import SimpleNamespace
        call = SimpleNamespace(type="tool_use", id="toolu_1", name="claude_code", input={})
        history = [{"role": "user", "content": "Start."}, {"role": "assistant", "content": [call]}]
        got = screens.side_question(history, "Do you want to proceed?")
        self.assertEqual(got[:2], history, "nothing before is changed, so it stays cached")
        self.assertEqual(got[2]["role"], "user")
        self.assertEqual([b["type"] for b in got[2]["content"]], ["tool_result", "text"])
        self.assertEqual(got[2]["content"][0]["tool_use_id"], "toolu_1")
        after_results = history + [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "x"}]}]
        got = screens.side_question(after_results, "Pick one")
        self.assertEqual([b["type"] for b in got[-1]["content"]], ["tool_result", "text"])

    def test_old_screens_are_trimmed_rarely_and_recent_ones_never(self):
        def turn(i, size):
            return [{"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}"}]},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "x" * size}]}]
        messages = [{"role": "user", "content": "Start."}]
        trimmed_at = []
        for i in range(80):
            messages += turn(i, 10_000)
            if screens.trim(messages):
                trimmed_at.append(i)
        self.assertGreaterEqual(len(trimmed_at), 2)
        self.assertGreater(min(b - a for a, b in zip(trimmed_at, trimmed_at[1:])), 5, "rarely, so the cache holds")
        recent = [m["content"][0]["content"] for m in messages[-screens.RECENT:] if m["role"] == "user"]
        self.assertTrue(all(len(c) == 10_000 for c in recent), "the last screens are whole")
        self.assertTrue(messages[2]["content"][0]["content"].endswith(screens.TRIMMED))
        self.assertEqual(messages[0]["content"], "Start.")

    def test_a_redrawn_command_line_reads_once_as_windows_shows_it(self):
        """PSReadLine redraws the line on each key; ConPTY sends cursor moves. Stripping them left
        "bbutbutl..." in round 3's Windows screens."""
        s = Screen(5, 40)
        s.feed("\x1b[?25l\x1b[2J\x1b[HPS C:\\Users\\walter> \x1b[?25h")
        for i in range(1, len("butler brief") + 1):
            s.feed(f"\x1b[{len('PS C:/Users/walter> ') + 1}G" + "butler brief"[:i] + "\x1b[K")
        self.assertEqual(s.lines(), ["PS C:\\Users\\walter> butler brief"])
        s.feed("\r\n\x1b[32mThis takes about half a minute.\x1b[0m\r\n")
        s.feed("\x1b[3;1HDone.\x1b[K\r\nline 4\r\nline 5\r\nline 6\r\n")
        self.assertEqual(s.lines()[0], "PS C:\\Users\\walter> butler brief", "a line that scrolls off is kept")
        self.assertEqual(s.lines()[1:4], ["This takes about half a minute.", "Done.", "line 4"])
        s.feed("ab\bc\x1b[2Dz")
        self.assertTrue(s.lines()[-1].startswith("zc"))

    def test_each_read_shows_what_changed_once(self):
        term = Terminal.__new__(Terminal)
        term._shown = []
        self.assertEqual(term.since(["PS C:\\> "]), "PS C:\\> \n")
        self.assertEqual(term.since(["PS C:\\> butler", "Hello"]), "PS C:\\> butler\nHello\n")
        self.assertEqual(term.since(["PS C:\\> butler", "Hello"]), "")
        self.assertEqual(term.since(["PS C:\\> butler", "Hello", "Name ›"]), "Name ›\n")

    def test_screens_read_as_plain_text(self):
        self.assertEqual(plain("\x1b[1mBold\x1b[0m\r\nnext\r"), "Bold\nnext\n")
        self.assertEqual(screens.page_text("<style>x{}</style><h1>Good morning</h1><p>Rain\n at 9</p>"), "Good morning Rain at 9")
        self.assertEqual(screens.page_text("<p>Ukraine&#x27;s &quot;truce&quot; &amp; more</p>"), 'Ukraine\'s "truce" & more')

    def test_trusting_the_folder_is_kept_where_claude_code_reads_it(self):
        """Round 4 (28 Oct): Butler's allow list (butler #74) never applied, because an SDK session
        ignores a folder's allow rules until the folder is trusted. The persona's Yes now records it."""
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / ".claude.json"
            config.write_text(json.dumps({"numStartups": 3, "projects": {"/elsewhere": {"x": 1}}}))
            screens.trust_folder(Path(tmp) / "Butler", config)
            data = json.loads(config.read_text())
            self.assertTrue(data["projects"][str(Path(tmp) / "Butler")]["hasTrustDialogAccepted"])
            self.assertEqual((data["numStartups"], data["projects"]["/elsewhere"]), (3, {"x": 1}))
            fresh = Path(tmp) / "none.json"
            screens.trust_folder("C:\\Users\\runner\\Butler", fresh)
            self.assertTrue(json.loads(fresh.read_text())["projects"]["C:\\Users\\runner\\Butler"]["hasTrustDialogAccepted"])


class WorkflowsParseTest(unittest.TestCase):
    def test_every_workflow_is_valid_yaml(self):
        """GitHub refuses a workflow that doesn't parse, without running it: a step name with
        ': ' in it once did that to personas.yml."""
        for path in sorted((HERE.parent / ".github" / "workflows").glob("*.yml")):
            with self.subTest(path.name):
                self.assertIn("jobs", yaml.safe_load(path.read_text()))

    def test_the_harness_python_has_the_time_zones_its_fixtures_need(self):
        """Windows' Python has no time-zone database: the harness's fixtures (Europe/Lisbon) need
        tzdata, or every Windows run stops before it starts, as round 2 did on 27 Oct."""
        for script in ("as-student.ps1", "as-student.sh"):
            with self.subTest(script):
                self.assertRegex((HERE / "personas" / script).read_text(), r"pip install [^\n]*tzdata")

    def test_a_persona_takes_the_students_path(self):
        """The shortcuts the old harness took (28 Oct audit) stay gone: no silent install, no
        Claude Code of the harness's own, no dry run of `butler claude`, not the runner's admin."""
        workflow = (HERE.parent / ".github" / "workflows" / "personas.yml").read_text()
        harness = (HERE / "personas" / "run_persona.py").read_text()
        windows = (HERE / "personas" / "as-student.ps1").read_text()
        self.assertNotIn("@anthropic-ai/claude-code", workflow, "Claude Code comes from `butler claude`")
        self.assertNotIn("\"BUTLER_CLAUDE_DRY_RUN\":", harness)
        # A silent install only where a double-click's windows take no click, and said as it happens.
        double_click = harness.index("threading.Thread(target=os.startfile, args=(str(self.downloads),)")
        silent = harness.index('"/VERYSILENT"')
        self.assertEqual(harness.count('"/VERYSILENT"'), 1)
        self.assertLess(double_click, silent)
        self.assertIn("self.difference(", harness[harness.rindex("if stuck:", 0, silent):silent])
        self.assertIn("as-student.sh", workflow)
        self.assertIn("as-student.ps1", workflow)
        self.assertIn('"BYB Test"', windows)
        self.assertIn('home="/Users/byb test"', (HERE / "personas" / "as-student.sh").read_text())
        self.assertIn("ANTHROPIC_API_KEY: ${{ inputs.budget != '0' && secrets.ANTHROPIC_API_KEY || '' }}", workflow,
                      "a rehearsal gets no key, so it can't spend anything")

    def test_a_round_plans_each_computer_and_keeps_its_ceiling(self):
        import plan
        personas = ["grandfather", "returner", "skimmer"]
        everything = plan.plan(personas, "", "all", 1, 6)
        self.assertEqual(len(everything), 9)
        self.assertEqual({(r["computer"], r["os"]) for r in everything},
                         {("mac", "macos-latest"), ("windows", "windows-2025"), ("windows-arm", "windows-11-arm")})
        self.assertTrue(all(r["platform"] == "windows" for r in everything if r["computer"] == "windows-arm"),
                        "Windows on ARM reads the Windows lessons")
        self.assertEqual([r["computer"] for r in plan.plan(personas, "skimmer", "both", 2, 6)],
                         ["mac", "mac", "windows", "windows"])
        self.assertEqual(len(plan.plan(personas, "", "windows-arm", 3, 0)), 9, "a rehearsal costs nothing")
        with self.assertRaises(SystemExit):
            plan.plan(personas, "", "all", 10, 6)    # 90 runs at $6
        with self.assertRaises(SystemExit):
            plan.plan(personas, "", "linux", 1, 6)
        with self.assertRaises(SystemExit):
            plan.plan(personas, "nobody", "all", 1, 6)


class ReportTest(unittest.TestCase):
    def run_(self, name, platform, seed, findings, cost=2.5, **checks):
        base = {"installed": True, "named": "Bento", "signed_in": True, "terms_agreed": True, "settings": True,
                "first_brief": True, "claude_code_opened": False, "lessons_reported": [], "tracebacks": 0}
        return {"persona": name.lower(), "name": name, "platform": platform, "seed": seed, "cards": ["a card"],
                "outcome": {"outcome": "finished", "why": ""}, "reached": "m2-tour", "lessons_read": 17,
                "lessons_total": 17, "notes": [], "report": {"findings": findings, "how_it_felt": "fine"},
                "checks": {**base, **checks}, "cost_usd": {"total": cost}, "minutes": 30}

    def test_findings_reported_most_often_come_first_and_costs_add_up(self):
        f = lambda lesson, sev: {"severity": sev, "lesson": lesson, "what_happened": "x", "expected": "", "suggestion": ""}
        runs = [self.run_("Walter", "mac", 1, [f("setup.email", "minor"), f("m1-claude-code", "blocker")]),
                self.run_("Priya", "windows", 1, [f("setup.email", "major")], tracebacks=2),
                self.run_("Jordan", "mac", 2, [f("setup.email", "polish")])]
        md = report.render(runs)
        self.assertIn("# Persona tests: 3 runs, $7.50", md)
        self.assertLess(md.index("### setup.email"), md.index("### m1-claude-code"), "three runs found it")
        self.assertIn("tracebacks on screen in 1 runs", md)
        self.assertIn("No run finished", report.render([]))


if __name__ == "__main__":
    unittest.main()
