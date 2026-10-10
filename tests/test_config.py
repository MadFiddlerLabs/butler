"""Guards the config: a bad edit must fail loudly, not silently drop rules. Runs against your
config/*.yaml when present, else the examples.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import unittest

from casa.budget import load_plan
from casa.config import load_settings, people
from casa.finance import load_rules


class ConfigTest(unittest.TestCase):
    def test_category_map_has_required_sections(self):
        r = load_rules()
        for key in ("map", "overrides", "rules"):
            self.assertIn(key, r, f"category_map lost its '{key}' section")
        self.assertIn("day_to_day", r["rules"])

    def test_fixed_lines_exist_in_the_budget(self):
        items = {l.item for l in load_plan().lines}
        rules = load_rules()
        named = rules["rules"].get("fixed_lines", []) + rules["rules"].get("netted_from_rent", [])
        drawer_line = ((rules.get("cash_drawer") or {}).get("recurring") or {}).get("budget_line")
        for item in named + ([drawer_line] if drawer_line else []):
            self.assertIn(item, items, f"budget line '{item}' isn't in the workbook")

    def test_settings_basics(self):
        s = load_settings()
        for key in ("timezone", "email", "calendar", "reminders", "news", "llm", "household", "portal"):
            self.assertIn(key, s)
        self.assertTrue(people(s), "household.adults is empty")
        if s.get("score_tracker"):
            self.assertLess(s["score_tracker"]["pass_score"], s["score_tracker"]["out_of"] + 1)


if __name__ == "__main__":
    unittest.main()
