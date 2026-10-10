"""Privacy invariants, checked on everything that leaves the Mac: emails, Claude prompts, the
log and the files the portal serves. ID numbers, phones and secrets must never appear; the news
call must see no household details. All data is invented (tests/fakes/fixtures.py).
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import unittest

from fakes import fixtures
from fakes.home import Home

SUBJECT = "Re: Bento · Saturday"


class PrivacyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = h = Home()
        h.add_reply("sam@example.com", SUBJECT, "When does Alex's residence card expire?", "example.com")
        h.set_actions({SUBJECT: [{"type": "question", "text": "When does Alex's residence card expire?"}]})
        cls.records = {flow: h.run(flow) for flow in ("daily", "replies", "weekly", "monthly", "portal")}
        cls.prompts = [p for r in cls.records.values() for p in r["prompts"]]
        cls.mails = [m for r in cls.records.values() for m in r["mails"]]

    @classmethod
    def tearDownClass(cls):
        cls.home.close()

    def outgoing(self) -> dict[str, str]:
        """Everything that leaves the Mac or lands in a log, by where it went."""
        out = {}
        for i, p in enumerate(self.prompts):
            out[f"prompt {i} ({p['schema']})"] = p["system"] + "\n" + p["prompt"]
        for i, m in enumerate(self.mails):
            out[f"mail {i} ({m['subject']})"] = "\n".join([m["subject"], m["text"], m["html"]])
        out["logs/butler.log"] = self.home.log()
        for flow, r in self.records.items():
            out[f"stderr {flow}"] = r["stderr"]
        return out

    def test_flows_ran(self):
        for flow, r in self.records.items():
            self.assertIsNone(r["error"], f"{flow}: {r['error']}")
        self.assertTrue(self.prompts and self.mails)
        self.assertIn("answer", [p["schema"] for p in self.prompts], "the question path didn't run")

    def test_no_id_numbers_or_phones_leave_the_mac(self):
        for where, text in self.outgoing().items():
            for secret in fixtures.ID_NUMBERS + fixtures.PHONES:
                self.assertNotIn(secret, text, f"{secret} leaked into {where}")

    def test_expiry_dates_do_reach_the_brief(self):
        # The heads-up uses document names and dates only. Proves the reference file was read.
        self.assertIn("Residence card", self.mails[0]["text"])

    def test_no_secret_appears_anywhere(self):
        texts = self.outgoing() | self.home.text_files("out", "data", "logs", "config", "projects")
        for where, text in texts.items():
            for secret in fixtures.SECRETS.values():
                self.assertNotIn(secret, text, f"a Keychain secret leaked into {where}")

    def test_news_call_sees_no_household_details(self):
        news = [p for p in self.prompts if p["schema"] == "news"]
        self.assertTrue(news)
        for p in news:
            text = p["system"] + p["prompt"]
            self.assertNotIn(fixtures.ABOUT_MARKER, text)
            self.assertNotIn(fixtures.FEEDBACK_MARKER, text)
            for name in ("Sam", "Alex", "Mia", "Pat", "Rivera"):
                self.assertNotIn(name, text, f"the news call saw the name {name}")

    def test_personal_calls_do_get_context(self):
        # The markers are live: the calls that should see household context do.
        prep = next(p for p in self.prompts if p["schema"].startswith("prep"))
        self.assertIn(fixtures.ABOUT_MARKER, prep["system"])
        self.assertIn(fixtures.FEEDBACK_MARKER, prep["system"])

    def test_news_injection_stays_inert(self):
        # The hostile story reaches the news call as data, no other call sees it, and mail only
        # ever goes to the household. (If the news call fails, raw headlines are shown as text.)
        for p in self.prompts:
            if p["schema"] != "news":
                self.assertNotIn("passport numbers", p["system"] + p["prompt"])
        for m in self.mails:
            self.assertLessEqual(set(m["to"]), {"sam@example.com", "alex@example.com"}, m["to"])

    def test_info_page_shows_ids_only_uncached(self):
        info = self.records["portal"]["portal"]["GET /info"]
        self.assertIn(fixtures.ID_NUMBERS[0], info["body"])
        self.assertEqual(info["cache_control"], "no-store")
        for route, r in self.records["portal"]["portal"].items():
            if route != "GET /info":
                for number in fixtures.ID_NUMBERS:
                    self.assertNotIn(number, r["body"], f"{number} shown on {route}")


if __name__ == "__main__":
    unittest.main()
