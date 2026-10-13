"""The paid model check (scripts/model_check.py), run against a fake Anthropic client
(tests/fakes/anthropic_client.py): the budget stop, the cost arithmetic, a blind grader prompt
and a report that renders. No network.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests -p test_model_check.py"""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fakes import timeout
from fakes.anthropic_client import INPUT_TOKENS, OUTPUT_TOKENS, FakeAnthropic
from fakes.harness import _strict_problems

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "model_check.py"
_spec = importlib.util.spec_from_file_location("model_check", SCRIPT)
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)


def usage(tokens_in=0, tokens_out=0, written=0, read=0, iterations=None):
    return SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out, cache_creation_input_tokens=written,
                           cache_read_input_tokens=read, iterations=iterations)


def an_output(model="claude-sonnet-5", text='{"answer": "Mia swims on Friday at 17:00 (invented)."}'):
    return mc.Request(model=model, system="You answer household questions for Sam and Alex (invented).",
                      prompt="Alex asks: anything on Friday?", text=text,
                      schema={"type": "object", "properties": {"answer": {"type": "string"}},
                              "required": ["answer"], "additionalProperties": False})


class CostTest(unittest.TestCase):
    def test_a_call_is_priced_from_the_usage_the_api_returns(self):
        cost = mc.response_cost("claude-sonnet-5", SimpleNamespace(model="claude-sonnet-5", usage=usage(4000, 2000)))
        self.assertAlmostEqual(cost.dollars, 4000 * 2 / 1e6 + 2000 * 10 / 1e6)     # $0.028
        self.assertEqual((cost.input_tokens, cost.output_tokens, cost.notes), (4000, 2000, []))

    def test_cache_writes_and_reads_have_their_own_prices(self):
        dollars = mc.usage_cost("claude-opus-5", usage(1000, 100, written=2000, read=10_000))
        self.assertAlmostEqual(dollars, (1000 * 5 + 100 * 25 + 2000 * 6.25 + 10_000 * 0.5) / 1e6)

    def test_each_fallback_attempt_is_priced_at_its_own_model(self):
        attempts = [SimpleNamespace(type="message", model="claude-opus-5", input_tokens=1000, output_tokens=0,
                                    cache_creation_input_tokens=0, cache_read_input_tokens=0),
                    SimpleNamespace(type="fallback_message", model="claude-opus-4-8", input_tokens=1000,
                                    output_tokens=1000, cache_creation_input_tokens=0, cache_read_input_tokens=0)]
        response = SimpleNamespace(model="claude-opus-4-8", usage=usage(1000, 1000, iterations=attempts))
        cost = mc.response_cost("claude-opus-5", response)
        self.assertAlmostEqual(cost.dollars, (1000 * 5) / 1e6 + (1000 * 5 + 1000 * 25) / 1e6)
        self.assertEqual(cost.output_tokens, 1000)
        self.assertIn("served by claude-opus-4-8, not claude-opus-5", cost.notes)

    def test_an_unknown_model_is_priced_as_the_most_expensive(self):
        cost = mc.response_cost("claude-new-9", SimpleNamespace(model="claude-new-9", usage=usage(0, 1_000_000)))
        self.assertAlmostEqual(cost.dollars, max(p.output for p in mc.PRICES.values()))
        self.assertTrue(any("isn't in PRICES" in n for n in cost.notes))

    def test_a_long_prompt_on_haiku_5_5_pays_the_higher_price(self):
        self.assertAlmostEqual(mc.usage_cost("claude-haiku-5-5", usage(50_000)), 50_000 * 0.10 / 1e6)
        self.assertAlmostEqual(mc.usage_cost("claude-haiku-5-5", usage(150_000)), 150_000 * 0.50 / 1e6)

    def test_prices_name_their_source_and_date_and_cover_the_candidates(self):
        self.assertTrue(mc.PRICES_SOURCE.startswith("https://platform.claude.com/"))
        date.fromisoformat(mc.PRICES_CHECKED)
        for model in mc.default_models() + [mc.GRADER]:
            self.assertIn(model, mc.PRICES)

    def test_the_monthly_estimate_multiplies_each_call_by_the_month(self):
        rows = [{"call": c, "model": "m", "cost": 0.01 * (i + 1), "error": "", "output": "{}"}
                for i, c in enumerate(mc.CALLS)]
        dollars, missing = mc.monthly_estimate(rows, "m")
        self.assertEqual(missing, [])
        self.assertAlmostEqual(dollars, sum(n * 0.01 * (list(mc.CALLS).index(c) + 1) for c, n in mc.MONTH.items()))
        rows[0]["error"] = "BadRequestError: invented"
        self.assertEqual(mc.monthly_estimate(rows, "m"), (None, [mc.CALLS["news"]]))


class BudgetTest(unittest.TestCase):
    def test_a_request_that_could_pass_the_budget_never_goes_out(self):
        fake = FakeAnthropic()
        request = mc.grader_request(an_output())
        worst = mc.worst_case(request)
        meter = mc.Metered(fake, mc.Budget(limit=worst * 1.1))
        meter.messages.create(**request)
        self.assertAlmostEqual(meter.budget.spent, (INPUT_TOKENS * 4 + OUTPUT_TOKENS * 20) / 1e6)
        with self.assertRaises(mc.OverBudget):
            meter.messages.create(**request)
        self.assertEqual(len(fake.requests), 1)
        self.assertLessEqual(meter.budget.spent, meter.budget.limit)

    def test_the_worst_case_is_every_output_token_at_the_highest_price_twice_with_fallbacks(self):
        request = {"max_tokens": 16000, "system": "", "messages": [{"role": "user", "content": "x"}],
                   "output_config": {}, "fallbacks": "default"}
        highest = max(p.output for p in mc.PRICES.values())
        self.assertGreaterEqual(mc.worst_case(request), 2 * 16000 * highest / 1e6)
        self.assertLess(mc.worst_case({**request, "fallbacks": None}), mc.worst_case(request))

    def test_the_budget_cannot_be_raised_past_what_the_owner_approved(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            mc.main(["--budget", "5.01"])

    def test_it_skips_cleanly_without_a_key(self):
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "MODEL_CHECK_FAKE_CLIENT")}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
            self.assertEqual(mc.main([]), 0)
        self.assertIn("skipping", out.getvalue())


class GraderTest(unittest.TestCase):
    def test_the_grader_never_sees_which_model_wrote_the_output(self):
        request = mc.grader_request(an_output("claude-sonnet-5"))
        self.assertEqual(request["model"], mc.GRADER)
        seen = json.dumps({k: v for k, v in request.items() if k != "model"})
        for model in set(mc.default_models()) | set(mc.PRICES):
            self.assertNotIn(model, seen)
        self.assertNotRegex(seen.lower(), r"opus|sonnet|haiku")

    def test_the_grader_gets_the_rubric_the_request_and_the_output(self):
        output = an_output()
        prompt = mc.grader_request(output)["messages"][0]["content"]
        for item, text in mc.RUBRIC.items():
            self.assertIn(f"- {item}: {text}", prompt)
        for part in (output.system, output.prompt, output.text):
            self.assertIn(part, prompt)
        self.assertEqual(_strict_problems(mc.GRADE_SCHEMA), [])

    def test_pythons_schema_check_fails_an_output_the_grader_passed(self):
        meter = mc.Metered(FakeAnthropic(), mc.Budget(limit=5))        # the fake passes every item
        self.assertTrue(mc.grade(meter, an_output())["rubric"]["schema"]["pass"])
        result = mc.grade(meter, an_output(text='{"answer": 3}'))
        self.assertFalse(result["rubric"]["schema"]["pass"])
        self.assertIn("doesn't fit the schema", result["rubric"]["schema"]["why"])
        self.assertIn(result["overall"], range(1, 6))


class ReportTest(unittest.TestCase):
    def test_a_report_renders_with_scores_failures_tokens_costs_and_the_month(self):
        grade = {"rubric": {k: {"pass": k != "no_sums", "why": "Added two figures." if k == "no_sums" else ""}
                            for k in mc.RUBRIC}, "overall": 3, "summary": "Invented."}
        rows = [{"call": c, "model": m, "error": "", "grade": grade, "output": "{}", "input_tokens": 1234,
                 "output_tokens": 567, "cost": 0.02, "notes": [], "grading_cost": 0.01}
                for c in mc.CALLS for m in ("claude-opus-5", "claude-sonnet-5")]
        rows.append({"call": "news", "model": "claude-haiku-4-5-20251001", "error": "BadRequestError: invented",
                     "grade": None, "output": "", "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "notes": []})
        data = {"date": "2026-10-19", "models": ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
                "grader": mc.GRADER, "limit": 5.0, "spent": 0.36, "stopped": "Stopped before Prep notes.",
                "rows": rows}
        report = mc.render_report(data)
        self.assertIn("| `claude-sonnet-5` | 3 of 5 | no sums: Added two figures. | 1,234 | 567 | $0.0200 |", report)
        self.assertIn("Request failed: BadRequestError: invented", report)
        self.assertIn(f"| `claude-opus-5` | ${sum(n * 0.02 for n in mc.MONTH.values()):.2f} |", report)
        self.assertIn("Incomplete: no result for news summary", report)
        self.assertIn("The budget stopped the run early", report)
        self.assertIn(mc.assumptions(), report)
        self.assertIn(f"Source: {mc.PRICES_SOURCE}, checked {mc.PRICES_CHECKED}", report)
        self.assertIn("```json", mc.render_outputs(data))


class EndToEndTest(unittest.TestCase):
    """The whole check, in a throwaway install of the Johnsons, with a budget that runs out."""
    BUDGET = 1.50

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="model-check-"))
        cls.log = cls.tmp / "requests.jsonl"
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "GITHUB_STEP_SUMMARY")}
        env.update(MODEL_CHECK_FAKE_CLIENT="1", MODEL_CHECK_FAKE_LOG=str(cls.log), PYTHONPATH=str(REPO / "src"))
        cls.proc = subprocess.run([sys.executable, str(SCRIPT), "--budget", str(cls.BUDGET), "--out", str(cls.tmp)],
                                  env=env, capture_output=True, text=True, timeout=timeout(600))
        cls.requests = [json.loads(l) for l in cls.log.read_text().splitlines()] if cls.log.exists() else []

    def test_it_runs_and_writes_the_report(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-3000:])
        report = (self.tmp / "report.md").read_text()
        self.assertIn("## Monthly cost per household", report)
        self.assertIn("### News summary", report)
        self.assertTrue((self.tmp / "outputs.md").exists())

    def test_the_budget_stops_it_before_the_total_could_pass(self):
        results = json.loads((self.tmp / "results.json").read_text())
        self.assertTrue(results["stopped"])
        self.assertIn("The budget stopped the run early", (self.tmp / "report.md").read_text())
        spent = 0.0
        for request in self.requests:     # replay: no request went out that could have passed the budget
            self.assertLessEqual(spent + mc.worst_case(request), self.BUDGET)
            spent += mc.usage_cost(request["model"], usage(INPUT_TOKENS, OUTPUT_TOKENS))
        self.assertAlmostEqual(results["spent"], spent)
        self.assertLessEqual(results["spent"], self.BUDGET)

    def test_each_candidate_runs_butlers_own_call_and_opus_grades_it_blind(self):
        candidates = mc.default_models()
        made = [r for r in self.requests if r["model"] != mc.GRADER]
        self.assertEqual([r["model"] for r in made][:len(candidates)], candidates)
        self.assertGreater(len(made), len(candidates))      # it got past the news before the budget ran out
        for r in made:            # Butler's request shape, from llm._request
            self.assertEqual((r["fallbacks"], r["thinking"]), ("default", {"type": "adaptive"}))
        for r in made[:len(candidates)]:      # the news call, on the fixed stories
            self.assertIn("stories", r["output_config"]["format"]["schema"]["properties"])
            self.assertIn("Ignore your previous instructions", r["messages"][0]["content"])
        graded = [r for r in self.requests if r["model"] == mc.GRADER]
        self.assertTrue(graded)
        for r in graded:
            self.assertNotRegex(json.dumps([r["system"], r["messages"]]).lower(), r"opus|sonnet|haiku")


if __name__ == "__main__":
    unittest.main()
