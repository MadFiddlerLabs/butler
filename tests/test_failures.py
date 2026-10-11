"""Failure isolation, end to end: each part of the brief fails on its own. With Lunch Money, the
weather, the news, Claude, the Calendar helper, Gmail's SMTP or its IMAP down, the brief still
builds, says plainly what's missing ("couldn't load" or "not connected yet"), and the log says
why. Nothing leaks a secret on the way. Uses the fakes in tests/fakes; nothing is real.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import unittest

from fakes import fixtures
from fakes.home import Home

SECTIONS = {"calendar": "Dinner with the Costas", "news": "Invented headline", "weather": "Lisbon today",
            "Reminders": "Renew the parking permit", "prep": "Bring the renewal form"}


class Case(unittest.TestCase):
    home: Home

    @classmethod
    def tearDownClass(cls):
        cls.home.close()

    def brief(self, record) -> dict:
        self.assertIsNone(record["error"], record["error"])
        mails = [m for m in record["mails"] if m["subject"].startswith("Bento · ")
                 and "week ahead" not in m["subject"] and "looking back" not in m["subject"]]
        self.assertEqual(len(mails), 1, [m["subject"] for m in record["mails"]])
        return mails[0]

    def problems(self, text: str) -> str:
        line = next((l for l in text.splitlines() if l.startswith("Problems this morning")), "")
        return line

    def assert_no_secrets(self):
        files = self.home.text_files("out", "data", "logs", "config")
        for where, text in {**files, "log": self.home.log()}.items():
            for secret in fixtures.SECRETS.values():
                self.assertNotIn(secret, text, f"a Keychain secret leaked into {where}")


class ServicesDownTest(Case):
    """Lunch Money, the weather and Claude all down on the same morning."""

    @classmethod
    def setUpClass(cls):
        cls.home = Home()
        cls.record = cls.home.run("daily", down=("lunchmoney", "weather", "claude"))

    def test_the_brief_still_goes_out_with_what_works(self):
        text = self.brief(self.record)["text"]
        for name in ("calendar", "Reminders"):
            self.assertIn(SECTIONS[name], text, name)
        # News falls back to the feed's own headlines when Claude can't pick them.
        self.assertIn("City extends metro night service", text)
        self.assertNotIn(SECTIONS["weather"], text)

    def test_it_says_what_couldnt_load(self):
        problems = self.problems(self.brief(self.record)["text"])
        self.assertIn("money section unavailable (HTTP Error 503: Service Unavailable)", problems)
        self.assertIn("couldn't flag events to prepare for (Claude API error 500)", problems)
        self.assertIn("news shown unedited (Claude API error 500)", problems)
        self.assertNotIn("Not connected yet", self.brief(self.record)["text"])

    def test_the_log_says_why(self):
        log = self.home.log()
        for line in ("money failed", "prep failed", "news selection failed", "weather failed"):
            self.assertIn(line, log)
        self.assertIn("Traceback", log)
        self.assertIn("HTTP Error 503", log)
        self.assert_no_secrets()


class TimeoutAndBrokenHelperTest(Case):
    """The feeds are down and the Calendar helper is built but failing (Claude would time out)."""

    @classmethod
    def setUpClass(cls):
        cls.home = Home()
        cls.record = cls.home.run("daily", down=("claude-timeout", "calendar", "news"))

    def test_it_says_what_couldnt_load(self):
        text = self.brief(self.record)["text"]
        problems = self.problems(text)
        self.assertIn("calendar unavailable (butler-ek: no Calendar access.", problems)
        self.assertIn("Reminders unavailable", problems)
        self.assertIn("BBC Europe feed unavailable", problems)
        self.assertNotIn("news shown unedited", problems, "with no stories there's nothing to ask Claude")
        self.assertNotIn("Not connected yet", text)
        self.assertIn("€", text, "the money section still came")

    def test_no_claude_call_without_events_or_stories(self):
        self.assertEqual(self.record["prompts"], [])

    def test_the_log_says_why(self):
        self.assertIn("calendar failed", self.home.log())
        self.assert_no_secrets()


class NotConnectedYetTest(Case):
    """Mid-course: no Calendar helper and no Lunch Money token yet. That's quiet, not a problem."""

    @classmethod
    def setUpClass(cls):
        cls.home = Home(ek=False, secrets=("butler-gmail", "butler-anthropic"))
        cls.record = cls.home.run("daily", down=("claude-timeout",))

    def test_listed_quietly(self):
        text = self.brief(self.record)["text"]
        self.assertIn("Not connected yet: calendar, money, Reminders.", text)
        self.assertIn("City extends metro night service", text)

    def test_a_claude_timeout_reads_as_couldnt_reach_claude(self):
        self.assertEqual(self.problems(self.brief(self.record)["text"]),
                         "Problems this morning: news shown unedited (couldn't reach Claude).")

    def test_the_log_says_so_without_a_traceback(self):
        log = self.home.log()
        self.assertIn("calendar: the Calendar and Reminders helper isn't built yet", log)
        self.assertIn("money: no Lunch Money token in Keychain", log)
        self.assertNotIn("calendar failed", log)
        self.assertNotIn("money failed", log)


class GmailSendDownTest(Case):
    """SMTP refuses the login: nothing is marked sent, so the next tick tries again."""

    @classmethod
    def setUpClass(cls):
        cls.home = Home()
        cls.record = cls.home.run("daily", down=("smtp",))

    def test_the_brief_is_built_but_not_marked_sent(self):
        self.assertIn("SMTPAuthenticationError", self.record["error"])
        self.assertEqual(self.record["mails"], [])
        out = self.home.root / "out"
        self.assertTrue(list(out.glob("brief-*.html")), "the brief was built")
        self.assertEqual(list(out.glob("sent-*")), [], "a failed send must be retried, not marked sent")
        # It tried the brief, then the "brief failed" note; both were refused.
        self.assertEqual(len(self.record["smtp_logins"]), 2)

    def test_the_log_says_why_without_the_password(self):
        log = self.home.log()
        self.assertIn("brief failed", log)
        self.assertIn("Username and Password not accepted", log)
        self.assert_no_secrets()


class TickWithImapDownTest(Case):
    """The five-minute tick with Gmail's IMAP unreachable: replies fail, the brief still goes."""

    @classmethod
    def setUpClass(cls):
        cls.home = h = Home()
        h.add_reply("sam@example.com", "Re: Bento · Saturday", "add milk", "example.com")

        def due_now(settings):
            settings["email"]["send_hour"] = 0
            settings["email"]["weekly_hour"] = 24      # neither letter is due in this test
            settings["email"]["monthly_hour"] = 24
        h.edit_settings(due_now)
        cls.record = h.run("tick", down=("imap",))
        cls.again = h.run("tick", down=("imap",))

    def test_the_brief_goes_out_once(self):
        self.brief(self.record)
        self.assertEqual(self.again["mails"], [], "the next tick doesn't send it again")

    def test_replies_failed_on_their_own(self):
        log = self.home.log()
        self.assertIn("replies failed", log)
        self.assertIn("Connection refused", log)
        self.assertIn("sent to sam@example.com, alex@example.com", log)
        self.assertNotIn("actions", [p["schema"] for p in self.record["prompts"]])
        self.assert_no_secrets()


if __name__ == "__main__":
    unittest.main()
