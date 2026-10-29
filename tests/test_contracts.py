"""Contracts between parts that can drift apart: Claude's schemas, Lunch Money's v2 API, the Swift
helper and its test stub, the reply allowlist, and the names the course depends on (skill steps,
scripts, commands).
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import re
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import jsonschema

from butler import llm, lunchmoney, monthly, replies, weekly
from butler.config import load_settings
from fakes import ek_stub, fixtures, harness, lunchmoney_v2
from fakes.harness import _strict_problems

REPO = Path(__file__).resolve().parents[1]
SWIFT = (REPO / "helpers" / "butler-ek" / "main.swift").read_text()
SRC = {p.name: p.read_text() for p in (REPO / "src" / "butler").glob("*.py")}
COURSE_STEPS = ["install", "calendar", "brain", "inbox", "replies", "money", "projects", "letters", "phone"]
SKILLS = {p.parent.name: p.read_text() for p in (REPO / ".claude" / "skills").glob("*/SKILL.md")}
# (a Mac-only command, what Windows does instead, where the Windows way must be: the same block, or
# the same `## ` section of the skill).
MAC_ONLY = [
    (r"store-secret\.sh", "butler secret", "block"),
    (r"security add-generic-password", "butler secret", "block"),
    (r"security find-generic-password", "system.has_secret", "block"),
    (r"launchd/install\.sh", "run.py schedule install", "block"),
    (r"launchctl", "Task Scheduler", "block"),
    (r"pmset", "\\Butler\\morning", "block"),
    (r"/Applications/Tailscale\.app", "tailscale.exe", "block"),
    (r"TextEdit", "Notepad", "block"),
    (r"Butler app", "Start menu", "block"),
    (r"bootstrap\.sh", "bootstrap.ps1", "block"),
    (r"bin/butler-ek", "gcal", "section"),
]
# (the Mac's spelling, Windows' spelling) of a command that works the same way on both.
TRANSLATED = [
    (r"\./run\.sh", ".venv/Scripts/python.exe run.py"),
    (r"bin/butler\b(?![-.])", "bin/butler.cmd"),
    (r"\.venv/bin/python", ".venv/Scripts/python.exe"),
    (r"`open <url>`", "-m webbrowser"),
]


def _blocks(text: str) -> list[str]:
    """A skill's blocks: a numbered or bulleted item with its indented lines, a table row, a heading
    or a paragraph. A blank line or an unindented item, row or heading starts the next."""
    blocks, current = [], []
    for line in text.splitlines():
        if not line.strip() or re.match(r"(\d+\.\s|- |\||#)", line):
            if current:
                blocks.append("\n".join(current))
            current = [line] if line.strip() else []
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


class SchemaTest(unittest.TestCase):
    SCHEMAS = {"news": llm.NEWS_SCHEMA, "prep": llm._prep_schema(False), "prep+phrase": llm._prep_schema(True),
               "actions": replies.ACTION_SCHEMA, "answer": replies.ANSWER_SCHEMA, "weekly": weekly.WRITE_SCHEMA,
               "monthly": monthly.WRITE_SCHEMA}

    def test_every_schema_is_valid_and_strict(self):
        for name, schema in self.SCHEMAS.items():
            jsonschema.Draft202012Validator.check_schema(schema)
            self.assertEqual(_strict_problems(schema), [], name)


class ClaudeNeverDoesSumsTest(unittest.TestCase):
    """Python computes every amount. Where Claude sees money, it's told to quote, not compute,
    and a figure Claude does give is labelled as its own."""

    def test_a_reply_answer_is_told_never_to_compute_amounts(self):
        asked = {}

        def ask(settings, system, prompt, schema):
            asked.update(system=system, prompt=prompt, schema=schema)
            return {"answer": "Invented."}
        money = ["Daily spending: €400 against €500 pace (invented)."]
        with mock.patch("butler.llm._ask_json", side_effect=ask), \
                mock.patch("butler.agenda.load_agenda", side_effect=RuntimeError("no calendar here")), \
                mock.patch("butler.finance.build"), mock.patch("butler.render.money_lines", return_value=money), \
                mock.patch("butler.projects.load", return_value=[]):
            replies.answer(load_settings(), "How much on food and fun together?", "Sam", date(2026, 10, 20))
        self.assertIn(replies.NO_SUMS, asked["system"])
        self.assertIn("Never add, subtract", replies.NO_SUMS)
        self.assertIn("€400 against €500 pace", asked["prompt"])
        self.assertIs(asked["schema"], replies.ANSWER_SCHEMA)

    def test_the_letters_idea_amounts_are_labelled_as_claudes(self):
        source = SRC["weekly.py"]
        self.assertIn("(Claude's rough estimate: about {money(i['per_month'])} a month)", source)
        self.assertIn("only when a figure in the numbers gives it", source)


class NoDeleteTest(unittest.TestCase):
    def test_reply_actions_cannot_delete(self):
        kinds = replies.ACTION_SCHEMA["properties"]["actions"]["items"]["properties"]["type"]["enum"]
        for kind in kinds:
            self.assertNotRegex(kind, r"delete|remove|cancel|^clear|erase", kind)

    def test_every_reply_action_is_handled(self):
        perform = SRC["replies.py"].split("def perform(")[1].split("\ndef ")[0]
        for kind in replies.ACTION_SCHEMA["properties"]["actions"]["items"]["properties"]["type"]["enum"]:
            if kind != "unclear":    # falls through to "I wasn't sure what to do with"
                self.assertIn(f'"{kind}"', perform, f"perform() doesn't handle {kind}")

    def test_budget_figures_reach_python_as_written(self):
        """Claude copies a budget figure as text; budget_changes.py parses and bounds it."""
        item = replies.ACTION_SCHEMA["properties"]["actions"]["items"]["properties"]
        self.assertEqual(item["amount"], {"type": ["string", "null"]})
        perform = SRC["replies.py"].split("def perform(")[1].split("\ndef ")[0]
        self.assertIn("budget_changes.apply(settings, action, said, today)", perform)

    def test_helper_has_no_delete_or_edit(self):
        self.assertNotIn("store.remove(", SWIFT)
        self.assertNotIn("removeCalendar", SWIFT)
        for command in ("delete-event", "update-event", "rename-reminder"):
            self.assertNotIn(f'case "{command}"', SWIFT)


class HelperContractTest(unittest.TestCase):
    def swift_commands(self) -> set[str]:
        return set(re.findall(r'^case "([a-z-]+)":', SWIFT, re.M))

    def test_stub_matches_the_real_helper(self):
        self.assertEqual(ek_stub.COMMANDS, self.swift_commands())

    def test_helper_checks_commands_before_asking_for_access(self):
        listed = set(re.findall(r'"([a-z-]+)"', SWIFT.split("let commands: Set<String> = [")[1].split("]")[0]))
        self.assertEqual(listed, self.swift_commands())
        self.assertLess(SWIFT.index("guard commands.contains(command)"), SWIFT.index("\nrequestAccess()"))

    def test_the_helper_answers_for_its_own_calendar_access(self):
        """macOS asks butler-ek for Calendar access, not Terminal or launchd's job, so one grant covers
        the 07:00 run. The re-run happens once, before anything touches EventKit, and falls back
        to running as before when the private call is missing."""
        self.assertIn('dlsym(UnsafeMutableRawPointer(bitPattern: -2), "responsibility_spawnattrs_setdisclaim")', SWIFT)
        self.assertIn("guard getenv(disclaimedKey) == nil else { return }", SWIFT)
        self.assertIn("POSIX_SPAWN_SETEXEC", SWIFT)
        first = SWIFT.index("\ndisclaimResponsibility()")
        self.assertLess(first, SWIFT.index("\nlet store = EKEventStore()"))
        self.assertLess(first, SWIFT.index("\nrequestAccess()"))

    def test_the_stubs_events_have_the_helpers_fields(self):
        """The stub's `events` rows carry exactly the keys the Swift helper emits, organiser too."""
        swift_event = SWIFT.split('let events = store.events(matching: predicate).map')[1].split("emit(events)")[0]
        swift_organizer = SWIFT.split("func organizer(")[1].split("\n}\n")[0]
        start = fixtures.datetime.combine(fixtures.today(), fixtures.time.min, fixtures.ZONE)
        rows = fixtures.events(start, start + timedelta(days=7))
        self.assertEqual({k for r in rows for k in r}, set(re.findall(r'"([a-z_]+)":', swift_event)))
        organizers = [r["organizer"] for r in rows if r["organizer"]]
        self.assertTrue(organizers)
        self.assertTrue(any(r["organizer"] is None for r in rows))
        for o in organizers:
            self.assertEqual(set(o), set(re.findall(r'"([a-z_]+)":', swift_organizer)))

    def test_python_calls_only_real_commands(self):
        called = set()
        for text in SRC.values():
            called |= set(re.findall(r'_ek\("([a-z-]+)"', text))
            called |= set(re.findall(r'butler_ek\(\), "([a-z-]+)"', text))
            called |= set(re.findall(r'calendar_source\.(?:run|ask)\(\["([a-z-]+)"', text))
        self.assertTrue(called)
        self.assertLessEqual(called, self.swift_commands(), called - self.swift_commands())


class LunchMoneyContractTest(unittest.TestCase):
    """The client, the fake and the fixtures against Lunch Money's v2 spec (a trimmed copy in
    tests/fixtures/lunchmoney_v2.json; the live contract compares it with the real one)."""

    def setUp(self):
        self.spec = lunchmoney_v2.snapshot()

    def test_the_client_calls_v2(self):
        self.assertEqual(lunchmoney.BASE, self.spec["base"])
        self.assertNotIn("dev.lunchmoney.app", "".join(SRC.values()))

    def test_butler_reads_only_fields_the_spec_always_returns(self):
        for path, fields in lunchmoney_v2.READS.items():
            required = set(self.spec["paths"][path]["object"]["required"])
            self.assertLessEqual(fields, required, f"{path}: {fields - required}")

    def test_butler_and_the_fake_use_only_parameters_the_spec_lists(self):
        for path, params in harness.LUNCH_MONEY_PARAMS.items():
            listed = set(self.spec["paths"][path]["params"])
            self.assertLessEqual(params, listed, f"{path}: {params - listed}")
            self.assertLessEqual(lunchmoney_v2.SENDS[path], params, path)

    def test_the_fixtures_have_the_v2_shapes(self):
        today = date(2026, 10, 20)
        samples = {"transactions": fixtures.transactions(today.replace(month=6), today),
                   "categories": fixtures.CATEGORIES, "manual_accounts": fixtures.MANUAL_ACCOUNTS,
                   "plaid_accounts": fixtures.PLAID_ACCOUNTS}
        for path, rows in samples.items():
            spec = self.spec["paths"][path]["object"]
            self.assertTrue(rows, path)
            for row in rows:
                self.assertLessEqual(set(spec["required"]), set(row), f"{path} misses a required field")
                self.assertLessEqual(set(row), set(spec["properties"]), f"{path} has a field v2 doesn't")

    def test_transactions_come_back_named_through_every_page(self):
        def get(path, **params):
            url = f"{lunchmoney.BASE}/{path}"
            return harness._lunch_money(path, {k: str(v) for k, v in params.items()}, url)
        today = date(2026, 10, 20)
        with mock.patch.object(lunchmoney, "get", side_effect=get), mock.patch.object(lunchmoney, "PAGE", 7):
            txs = lunchmoney.transactions(today - timedelta(days=100), today)
        self.assertEqual(len(txs), len(fixtures.TRANSACTIONS))
        self.assertEqual(len({t["id"] for t in txs}), len(txs))
        bills = next(t for t in txs if t["payee"] == "Lisboa Energia")
        self.assertEqual((bills["category_name"], bills["plaid_account_name"], bills["asset_name"],
                          bills["institution_name"], bills["is_income"], bills["exclude_from_totals"]),
                         ("Utilities", "Everyday Checking", None, "Banco Exemplo", False, False))
        mystery = next(t for t in txs if t["payee"].startswith("Mystery"))
        self.assertEqual(mystery["category_name"], "")       # uncategorized: no category_id

    def test_a_manual_account_transaction_is_named_after_its_account(self):
        tx = {"id": 1, "category_id": None, "plaid_account_id": None, "manual_account_id": 1}
        named = lunchmoney.named(tx, {}, {}, {a["id"]: a for a in fixtures.MANUAL_ACCOUNTS})
        self.assertEqual((named["asset_name"], named["plaid_account_name"]), ("Cash drawer", None))


class CourseContractTest(unittest.TestCase):
    """buildyourbutler.com links to these names. Renaming one means changing the course too."""

    def test_setup_skill_has_every_course_step(self):
        skill = (REPO / ".claude" / "skills" / "butler-setup" / "SKILL.md").read_text()
        hint = re.search(r'argument-hint: "\[(.*)\]"', skill).group(1)
        self.assertEqual([s.strip() for s in hint.split("|")], COURSE_STEPS)
        for step in COURSE_STEPS:
            self.assertRegex(skill, rf"(?m)^## {step} \(", f"no section for {step}")

    def test_readme_lists_every_step(self):
        readme = (REPO / "README.md").read_text()
        for step in COURSE_STEPS:
            self.assertIn(f"`/butler-setup {step}`", readme)

    def test_readme_shows_the_no_account_demo_first_and_has_no_placeholders(self):
        readme = (REPO / "README.md").read_text()
        demo = readme.index("## Try it in two commands, no account")
        self.assertLess(demo, readme.index("## Get started"))
        self.assertIn("scripts/bootstrap.sh", readme[demo:demo + 600])
        self.assertIn("./run.sh preview", readme[demo:demo + 600])
        self.assertNotIn("TODO", readme)
        self.assertNotIn("coming soon", readme.lower())

    def test_named_scripts_and_skills_exist(self):
        for path in ("scripts/install.sh", "scripts/bootstrap.sh", "scripts/store-secret.sh",
                     "helpers/butler-ek/build.sh", "launchd/install.sh",
                     ".claude/skills/butler-tune/SKILL.md", ".claude/skills/butler-doctor/SKILL.md"):
            self.assertTrue((REPO / path).exists(), path)
        self.assertIn('"$cmd" = "preview"', (REPO / "run.sh").read_text())

    def test_the_doctor_skips_the_developer_test_suite(self):
        """A household's install has no requirements-dev.txt packages, so the whole suite fails
        there and takes over ten minutes: the doctor reported 32 false failures (#34)."""
        doctor = (REPO / ".claude/skills/butler-doctor/SKILL.md").read_text()
        rows = [line for line in doctor.splitlines() if line.startswith("|")]
        self.assertFalse([line for line in rows if "unittest discover" in line])

    def test_the_butler_subcommands_the_course_names(self):
        """The website's contract check reads these from cli.py's add_parser calls and bin/butler."""
        parsed = set(re.findall(r'add_parser\("([a-z][a-z-]*)"', SRC["cli.py"]))
        self.assertLessEqual({"name", "town", "adults", "brief", "status", "progress", "claude", "claude-mode",
                              "doctor", "portal", "update", "help", "resume"}, parsed)
        self.assertIn('"${1:-}" = "setup"', (REPO / "bin" / "butler").read_text())
        # `butler resume` works before Python exists too: it's what the Butler app runs.
        self.assertIn('"${1:-}" = "resume"', (REPO / "bin" / "butler").read_text())
        # On Windows, bin/butler.cmd hands every subcommand to cli.py, `butler setup` included.
        self.assertIn('run.py" cli %*', (REPO / "bin" / "butler.cmd").read_text())
        self.assertLessEqual({"setup", "secret"}, parsed)

    def test_claude_code_starts_ask_first_in_the_butler_folder(self):
        """The course and the site promise "Claude asks before it acts" (council #204). Claude Code
        starts in auto mode by default, and a project's settings may set any starting mode but
        auto and bypassPermissions (code.claude.com/docs/en/permission-modes). `default` is the
        one that asks before each change; `butler claude` passes the household's own choice."""
        import json
        settings = json.loads((REPO / ".claude" / "settings.json").read_text())
        self.assertEqual(settings["permissions"]["defaultMode"], "default")
        self.assertNotIn("settings.json", (REPO / ".gitignore").read_text())

    def test_only_the_read_only_checks_and_the_videos_run_without_asking(self):
        """The persona rounds asked about `butler progress` and `butler status` again and again,
        and the owner agreed (28 Oct 2026) that they, and `butler doctor`, run without asking:
        they only read (progress also sends the lesson ticks the skill means it to). On 29 Oct the
        owner added `butler watch`, which each /butler-setup step runs first: it opens the module's
        video page and notes that it did. Nothing else is pre-approved. On Windows, Claude Code
        often uses its PowerShell tool, which Bash rules don't cover, so each has PowerShell rules
        too (owner, 28 Oct)."""
        import json
        allow = json.loads((REPO / ".claude" / "settings.json").read_text())["permissions"]["allow"]
        for rule in allow:
            m = (re.fullmatch(r"Bash\((butler|bin/butler|\./bin/butler|bin/butler\.cmd) "
                              r"(progress|status|doctor|watch)( \*)?\)", rule)
                 or re.fullmatch(r"PowerShell\((butler|\.\\bin\\butler\.cmd|bin\\butler\.cmd|bin/butler\.cmd|\./bin/butler\.cmd) "
                                 r"(progress|status|doctor|watch)( \*)?\)", rule))
            self.assertTrue(m, rule)
        self.assertEqual(len(allow), 72)
        self.assertIn("PowerShell(.\\bin\\butler.cmd progress)", allow, "the form Claude used in round 5")
        self.assertIn("Bash(bin/butler watch *)", allow, "the form the setup skill runs")

    def test_commands_run_where_claude_code_opened_without_changing_folders(self):
        """Persona round 5 (28 Oct): on Windows, Claude began nearly every command with
        `Set-Location <the Butler folder>;`, about 40 permission questions, each a compound command
        no allow rule matches. Every skill and CLAUDE.md say not to, and no command they give
        changes folders."""
        student = (REPO / "CLAUDE.md").read_text().split("## Talking to someone learning")[1].split("\n## ")[0]
        for name, text in [*SKILLS.items(), ("CLAUDE.md, students", student)]:
            flat = " ".join(text.split())
            self.assertIn("`Set-Location`", flat, name)
            self.assertRegex(flat, r"[Nn]ever (change folders first|start one with `cd`)", name)
        for name, text in SKILLS.items():
            for block in _blocks(text):
                for line in block.splitlines():
                    self.assertNotRegex(line.strip(), r"^(cd|Set-Location)\b", f"{name}: {line.strip()}")

    def test_progress_and_feedback_are_checked_not_guessed(self):
        """Persona rounds 4 to 6: Claude told students a module was skipped, then "not ticked yet",
        with no check; and /butler-tune looked for feedback sent a minute before, which Butler
        reads only every five minutes."""
        setup = " ".join(SKILLS["butler-setup"].split())
        self.assertIn("Never say a module is done, not done, ticked or skipped from this conversation alone", setup)
        self.assertIn("Run `bin/butler progress` first", setup)
        self.assertIn("Butler reads replies every five minutes", " ".join(SKILLS["butler-tune"].split()))

    def test_no_skill_runs_the_whole_test_suite_in_a_household(self):
        """A persona (28 Oct) watched Claude start the whole suite at Module 7 and keep mentioning it:
        it takes over ten minutes and, without requirements-dev.txt, fails where nothing is wrong.
        The skills and the household half of CLAUDE.md name it only to say don't."""
        household = (REPO / "CLAUDE.md").read_text().split("## When the user runs Butler")[1].split("\n## ")[0]
        for name, text in [*SKILLS.items(), ("CLAUDE.md, household", household)]:
            self.assertNotIn("full test suite passes", text, name)
            self.assertNotRegex(text, r"(?i)run the tests\b", name)
            for line in [p for p in re.split(r"\n\s*\n|\n(?=\s*(?:\d+\.|-) )", text) if "unittest discover" in p]:
                self.assertIn("Don't", line, f"{name}: {line.strip()[:80]}")

    def test_the_skills_give_windows_students_the_windows_way(self):
        """Butler runs on Windows too (#40), and the skills are written for a Mac. A Mac-only
        command must sit in the same step, row or paragraph as Windows' own way, so Claude never
        hands a Windows student a command that doesn't exist there."""
        for name, skill in SKILLS.items():
            # Each skill says how to tell the platform, the same way.
            self.assertIn("`darwin` for a Mac and `win32` for Windows", " ".join(skill.split()), name)
            blocks, sections = _blocks(skill), skill.split("\n## ")
            for mac, windows, scope in MAC_ONLY:
                if not re.search(mac, skill):
                    continue
                where = {"block": blocks, "section": sections}[scope]
                for part in where:
                    if re.search(mac, part):
                        flat = " ".join(part.split())
                        self.assertIn(windows, flat, f"{name}: {mac} without {windows}: {flat[:120]}")
                        self.assertIn("Windows", flat, f"{name}: {mac} isn't marked Mac-only: {flat[:120]}")
            # A command that's only spelled differently is translated once, in the skill's own table
            # or sentence: one block that names both spellings.
            for mac, windows in TRANSLATED:
                if re.search(mac, skill):
                    self.assertTrue(any(re.search(mac, b) and windows in b for b in blocks),
                                    f"{name}: no Windows spelling for {mac}")

    def test_keychain_names_agree(self):
        store = (REPO / "scripts" / "store-secret.sh").read_text()
        for name in ("butler-gmail", "butler-anthropic", "butler-lunchmoney"):
            self.assertIn(name, store)
            self.assertIn(f'"{name}"', "".join(SRC.values()))


if __name__ == "__main__":
    unittest.main()
