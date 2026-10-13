"""What Butler spends on Claude: each call's model and token counts recorded (and nothing else),
priced in Python, and shown on the portal's Money page as an estimate, never in the emails.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests -p test_claude_usage.py"""

import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from butler import claude_usage, llm, portal
from fakes.anthropic_client import FakeAnthropic

REPO = Path(__file__).resolve().parents[1]
SETTINGS = {"timezone": "Europe/Lisbon", "llm": {"model": "claude-sonnet-5-5", "effort": "medium"},
            "portal": {"url": "https://mac.tailnet.test", "allowed_logins": ["sam@example.com"]}}
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"],
          "additionalProperties": False}


def row(day: str, model: str = "claude-sonnet-5-5", tokens_in: int = 0, tokens_out: int = 0) -> str:
    return json.dumps({"day": day, "model": model, "input_tokens": tokens_in, "output_tokens": tokens_out,
                       "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0})


class UsageTest(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        self.path = folder / "data" / "claude_usage.jsonl"
        p = mock.patch.object(claude_usage, "USAGE", self.path)
        p.start()
        self.addCleanup(p.stop)

    def write(self, *lines: str) -> None:
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text("\n".join(lines) + "\n")

    def test_each_call_records_its_model_and_tokens_and_nothing_else(self):
        client = FakeAnthropic(input_tokens=3000, output_tokens=1500)
        with mock.patch.object(llm, "_client", return_value=client):
            llm._request(SETTINGS, "About the Johnsons (invented).", "Sam asks: what's on Friday?", SCHEMA)
        text = self.path.read_text()
        self.assertEqual(json.loads(text), {"day": json.loads(text)["day"], "model": "claude-sonnet-5-5",
                                            "input_tokens": 3000, "output_tokens": 1500,
                                            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0})
        date.fromisoformat(json.loads(text)["day"])
        for private in ("Johnsons", "Sam", "Friday", "Invented"):
            self.assertNotIn(private, text)

    def test_a_fallback_records_each_attempt_at_its_own_model(self):
        tries = [SimpleNamespace(type="message", model="claude-sonnet-5-5", input_tokens=1000, output_tokens=0),
                 SimpleNamespace(type="fallback_message", model="claude-opus-4-8", input_tokens=1000,
                                 output_tokens=500)]
        response = SimpleNamespace(model="claude-opus-4-8", usage=SimpleNamespace(iterations=tries))
        claude_usage.record(SETTINGS, response)
        self.assertEqual([json.loads(l)["model"] for l in self.path.read_text().splitlines()],
                         ["claude-sonnet-5-5", "claude-opus-4-8"])

    def test_recording_never_stops_a_call(self):
        self.path.mkdir(parents=True)      # a folder where the file should be: the write fails
        client = FakeAnthropic()
        with mock.patch.object(llm, "_client", return_value=client), self.assertLogs("butler", "WARNING"):
            self.assertEqual(llm._request(SETTINGS, "s", "p", SCHEMA), {"answer": "Invented."})
            claude_usage.record({}, SimpleNamespace())     # no settings, no usage: still no exception

    def test_the_month_is_priced_in_python(self):
        self.write(row("2026-10-01", tokens_in=1_000_000),                       # $2.00
                   row("2026-10-12", "claude-opus-5-5", tokens_out=100_000),      # $2.00
                   row("2026-09-30", tokens_in=5_000_000),                        # last month
                   "not json", '{"day": "2026-10-03"}')                          # skipped
        self.assertAlmostEqual(claude_usage.month_spend(date(2026, 10, 13)), 4.00)
        self.assertAlmostEqual(claude_usage.month_spend(date(2026, 9, 1)), 10.00)
        self.assertEqual(claude_usage.month_spend(date(2026, 11, 1)), 0)

    def test_an_unknown_model_is_priced_high(self):
        self.write(row("2026-10-01", "claude-new-9", tokens_out=1_000_000))
        self.assertAlmostEqual(claude_usage.month_spend(date(2026, 10, 2)),
                               max(p.output for p in claude_usage.PRICES.values()))

    def test_the_line_says_spend_and_limit(self):
        self.assertIsNone(claude_usage.summary(SETTINGS, date(2026, 10, 13)), "no call yet: no line")
        self.write(row("2026-10-01", tokens_in=600_000))                         # $1.20
        with_limit = {**SETTINGS, "llm": {**SETTINGS["llm"], "monthly_limit": 5}}
        self.assertEqual(claude_usage.summary(with_limit, date(2026, 10, 13)),
                         "Claude this month: about $1.20 of your $5 limit.")
        self.assertEqual(claude_usage.summary(SETTINGS, date(2026, 10, 13)), "Claude this month: about $1.20.")
        for odd in ("", None, "five", [5]):
            odd_limit = {**SETTINGS, "llm": {**SETTINGS["llm"], "monthly_limit": odd}}
            self.assertEqual(claude_usage.summary(odd_limit, date(2026, 10, 13)), "Claude this month: about $1.20.")
        self.write(row("2026-10-01", tokens_in=1000))                             # $0.002
        self.assertEqual(claude_usage.summary(with_limit, date(2026, 10, 13)),
                         "Claude this month: less than $0.01 of your $5 limit.")
        self.assertEqual(claude_usage.dollars(12.5), "$12.50")

    def test_the_money_page_shows_it_as_an_estimate_with_or_without_lunch_money(self):
        self.write(row(f"{date.today():%Y-%m}-01", tokens_in=600_000))
        settings = {**SETTINGS, "llm": {**SETTINGS["llm"], "monthly_limit": 10}}
        with mock.patch.object(portal, "load_settings", return_value=settings), \
                mock.patch.object(portal, "_today", return_value=date.today()), \
                mock.patch.object(portal, "_money", side_effect=RuntimeError("not connected yet")):
            html = portal.money_page()
        self.assertIn("Claude this month: about $1.20 of your $10 limit.", html)
        self.assertIn("An estimate from Butler's own calls; the Anthropic Console has the bill.", html)


class OneTableTest(unittest.TestCase):
    def test_the_model_check_prices_with_the_same_table(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("model_check", REPO / "scripts" / "model_check.py")
        mc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mc)
        self.assertIs(mc.PRICES, claude_usage.PRICES)
        self.assertIs(mc.usage_cost, claude_usage.usage_cost)

    def test_the_emails_never_show_it(self):
        for module in ("render.py", "daily.py", "weekly.py", "monthly.py", "replies.py", "mailer.py"):
            text = (REPO / "src" / "butler" / module).read_text()
            self.assertNotIn("claude_usage", text, module)
            self.assertNotIn("Claude this month", text, module)


if __name__ == "__main__":
    unittest.main()
