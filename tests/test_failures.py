"""Failure isolation, end to end: each part of the brief fails on its own. With Lunch Money, the
weather, the news, Claude, the Calendar helper, Gmail's SMTP or its IMAP down, the brief still
builds, says plainly what's missing ("couldn't load" or "not connected yet"), and the log says
why. Nothing leaks a secret on the way. Uses the fakes in tests/fakes; nothing is real.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import shutil
import smtplib
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from butler import mailer, tick
from fakes import fixtures
from fakes.home import Home

SETTINGS = {"email": {"sender_name": "Bento", "sender": "bento@example.com", "recipients": ["sam@example.com"]}}

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
    """SMTP refuses the login: the brief is built once and waits in the outbox. The next tick re-sends
    it as built, with no new Claude calls."""

    @classmethod
    def setUpClass(cls):
        cls.home = h = Home()

        def nothing_else_due(settings):
            settings["email"]["send_hour"] = 0
            settings["email"]["weekly_hour"] = 24
            settings["email"]["monthly_hour"] = 24
        h.edit_settings(nothing_else_due)
        cls.record = h.run("daily", down=("smtp",))
        cls.queued = sorted(p.name for p in (h.root / "data" / "outbox").glob("*.eml"))
        cls.again = h.run("tick")

    def test_the_brief_is_built_once_and_queued(self):
        self.assertIsNone(self.record["error"], self.record["error"])
        self.assertEqual(self.record["mails"], [])
        self.assertEqual(len(self.record["smtp_logins"]), 1, "no failure note: Gmail would refuse that too")
        out = self.home.root / "out"
        self.assertTrue(list(out.glob("brief-*.html")), "the brief was built")
        self.assertEqual(len(list(out.glob("sent-*"))), 1, "built today: the next tick doesn't rebuild it")
        self.assertEqual(len(self.queued), 1)
        self.assertRegex(self.queued[0], r"^brief-\d{4}-\d{2}-\d{2}\.eml$")

    def test_the_next_tick_sends_it_without_asking_claude_again(self):
        self.assertIsNone(self.again["error"], self.again["error"])
        self.assertEqual(self.again["prompts"], [])
        self.brief(self.again)
        self.assertIn(SECTIONS["prep"], self.brief(self.again)["text"])
        self.assertEqual(sorted(self.brief(self.again)["images"]), ["categories.png", "mtd.png"])
        self.assertEqual(list((self.home.root / "data" / "outbox").glob("*.eml")), [])

    def test_the_log_says_why_without_the_password(self):
        log = self.home.log()
        self.assertIn("send failed, queued brief-", log)
        self.assertIn("Username and Password not accepted", log)
        self.assertIn("brief queued; it goes out when Gmail takes it", log)
        self.assertIn("outbox: sent brief-", log)
        self.assert_no_secrets()


class OutboxBackoffTest(unittest.TestCase):
    """While Gmail keeps refusing, the outbox is tried on the next two ticks, then once an hour, and
    new mail waits with it. Nothing waits more than three days. The clock is simulated."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        # The clock starts at the real time, so queued files' real ages line up with it.
        self.start = time.time()
        self.clock, self.up, self.tries, self.sent = [self.start], [False], [], []
        test = self

        class SMTP:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *e):
                return False

            def login(self, *a):
                test.tries.append(test.clock[0])
                if not test.up[0]:
                    raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted.")

            def send_message(self, msg):
                test.sent.append(msg["Subject"])
        for p in (mock.patch.object(mailer, "OUTBOX", self.root / "outbox"),
                  mock.patch.object(mailer.time, "time", side_effect=lambda: self.clock[0]),
                  mock.patch.object(mailer.smtplib, "SMTP_SSL", SMTP),
                  mock.patch.object(mailer, "keychain", return_value="x")):
            p.start()
            self.addCleanup(p.stop)

    def at(self, minutes: float) -> None:
        self.clock[0] = self.start + minutes * 60

    def queue(self, key: str, subject: str) -> bool:
        return mailer.send_or_queue(SETTINGS, key, subject, "text")

    def test_fast_tries_then_hourly_then_everything_goes(self):
        with self.assertLogs("butler", "INFO"):
            self.assertFalse(self.queue("brief-2026-10-12", "Brief"))
            for minute in range(5, 125, 5):
                self.at(minute)
                if minute == 30:
                    self.assertFalse(self.queue("weekly-2026-41", "Letter"))   # waits, untried
                if minute == 70:
                    self.up[0] = True
                mailer.flush(SETTINGS)
        tried = sorted({round((t - self.start) / 60) for t in self.tries})
        self.assertEqual(tried, [0, 5, 10, 70], "two quick retries, then an hour after the last")
        self.assertEqual(self.sent, ["Brief", "Letter"], "oldest first")
        self.assertEqual(list((self.root / "outbox").glob("*")), [], "sent mail and the refusal count are gone")

    def test_a_message_unsent_for_three_days_is_dropped(self):
        with self.assertLogs("butler", "WARNING") as logs:
            self.queue("brief-2026-10-12", "Brief")
            self.at(3 * 24 * 60 + 1)
            self.up[0] = True
            self.assertEqual(mailer.flush(SETTINGS), 0)
        self.assertEqual(self.sent, [])
        self.assertIn("dropped brief-2026-10-12.eml", "\n".join(logs.output))

    def test_a_working_gmail_sends_at_once_and_queues_nothing(self):
        self.up[0] = True
        self.assertTrue(self.queue("brief-2026-10-12", "Brief"))
        self.assertEqual(self.sent, ["Brief"])
        self.assertFalse((self.root / "outbox").exists())


class TickKeepsGoingTest(unittest.TestCase):
    """A letter that raises (its failure note included) or a broken outbox never stops the brief."""

    def test_the_brief_still_runs(self):
        ran = []
        with mock.patch.object(tick, "ROOT", Path(tempfile.mkdtemp())) as root, \
             mock.patch.object(tick, "load_settings", return_value={"timezone": "Europe/Lisbon"}), \
             mock.patch.object(tick.mailer, "flush", side_effect=OSError("outbox unreadable")), \
             mock.patch.object(tick.replies, "process"), mock.patch.object(tick.updates, "tick"), \
             mock.patch.object(tick.progress, "sync"), \
             mock.patch.object(tick, "_weekly_if_due", side_effect=RuntimeError("letter and note both failed")), \
             mock.patch.object(tick, "_monthly_if_due", side_effect=RuntimeError("look-back failed")), \
             mock.patch.object(tick.daily, "main", side_effect=lambda: ran.append("brief")), \
             mock.patch.object(tick.logging, "basicConfig"), mock.patch.object(sys, "argv", ["tick"]), \
             self.assertLogs("butler", "ERROR") as logs:
            self.addCleanup(shutil.rmtree, root, True)
            tick.main()
        self.assertEqual(ran, ["brief"])
        text = "\n".join(logs.output)
        for line in ("outbox failed", "weekly letter failed", "monthly look-back failed"):
            self.assertIn(line, text)


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
