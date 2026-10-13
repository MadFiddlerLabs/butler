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

    def test_keychain_names_agree(self):
        store = (REPO / "scripts" / "store-secret.sh").read_text()
        for name in ("butler-gmail", "butler-anthropic", "butler-lunchmoney"):
            self.assertIn(name, store)
            self.assertIn(f'"{name}"', "".join(SRC.values()))


if __name__ == "__main__":
    unittest.main()
