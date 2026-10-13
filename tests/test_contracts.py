"""Contracts between parts that can drift apart: Claude's schemas, the Swift helper and its test
stub, the reply allowlist, and the names the course depends on (skill steps, scripts, commands).
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import re
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import jsonschema

from butler import llm, monthly, replies, weekly
from butler.config import load_settings
from fakes import ek_stub
from fakes.harness import _strict_problems

REPO = Path(__file__).resolve().parents[1]
SWIFT = (REPO / "helpers" / "butler-ek" / "main.swift").read_text()
SRC = {p.name: p.read_text() for p in (REPO / "src" / "butler").glob("*.py")}
COURSE_STEPS = ["install", "calendar", "brain", "inbox", "replies", "money", "projects", "letters", "phone"]


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

    def test_python_calls_only_real_commands(self):
        called = set()
        for text in SRC.values():
            called |= set(re.findall(r'_ek\("([a-z-]+)"', text))
            called |= set(re.findall(r'butler_ek\(\), "([a-z-]+)"', text))
        self.assertTrue(called)
        self.assertLessEqual(called, self.swift_commands(), called - self.swift_commands())


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

    def test_named_scripts_and_skills_exist(self):
        for path in ("scripts/install.sh", "scripts/bootstrap.sh", "scripts/store-secret.sh",
                     "helpers/butler-ek/build.sh", "launchd/install.sh",
                     ".claude/skills/butler-tune/SKILL.md", ".claude/skills/butler-doctor/SKILL.md"):
            self.assertTrue((REPO / path).exists(), path)
        self.assertIn('"$cmd" = "preview"', (REPO / "run.sh").read_text())

    def test_keychain_names_agree(self):
        store = (REPO / "scripts" / "store-secret.sh").read_text()
        for name in ("butler-gmail", "butler-anthropic", "butler-lunchmoney"):
            self.assertIn(name, store)
            self.assertIn(f'"{name}"', "".join(SRC.values()))


if __name__ == "__main__":
    unittest.main()
