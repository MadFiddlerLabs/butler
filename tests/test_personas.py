"""The persona tests' free parts (tests/personas): the personas themselves, the line the harness
reads to open Claude Code, the terminal text and the report. The runs themselves are paid and CI
only (.github/workflows/personas.yml).

Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "personas"))
import report  # noqa: E402
import screens  # noqa: E402
from terminal import Screen, Terminal, plain  # noqa: E402


class PersonasTest(unittest.TestCase):
    def test_each_persona_is_complete_and_invented(self):
        spec = yaml.safe_load((HERE / "personas" / "personas.yaml").read_text())
        self.assertGreaterEqual(len(spec["personas"]), 3)
        for key, p in spec["personas"].items():
            with self.subTest(key):
                self.assertTrue(p["name"] and len(p["who"]) > 200)
                self.assertGreaterEqual(len(p["deck"]), spec["draw"] * 2, "enough cards that runs differ")
                self.assertEqual(len(set(p["deck"])), len(p["deck"]))

    def test_the_harness_reads_butlers_own_dry_run_line(self):
        """cli.open_claude prints this with BUTLER_CLAUDE_DRY_RUN; change both together."""
        cli = (HERE.parent / "src" / "butler" / "cli.py").read_text()
        self.assertIn('''say(f"(dry run) would run: {' '.join(argv[:-1])} \\"{prompt}\\" in {ROOT}")''', cli)
        line = ('(dry run) would run: /usr/local/bin/claude --permission-mode default "Hello.\nIt\'s "Module 1"." in '
                '/Users/sam/Butler\nruner@mac Butler % ')
        m = screens.DRY_CLAUDE.search(line)
        self.assertEqual(m.groups(), ("/usr/local/bin/claude", "default", "Hello.\nIt's \"Module 1\".", "/Users/sam/Butler"))

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
        workflow = (HERE.parent / ".github" / "workflows" / "personas.yml").read_text()
        self.assertRegex(workflow, r"pip install [^\n]*tzdata")


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
