"""Failure isolation, end to end: each part of the brief fails on its own. With Lunch Money, the
weather, the news, Claude, the Calendar helper, Gmail's SMTP or its IMAP down, the brief still
builds, says plainly what's missing ("couldn't load" or "not connected yet"), and the log says
why. Nothing leaks a secret on the way. An update's outcome report never stops the update. Uses
the fakes in tests/fakes; nothing is real.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import shutil
import smtplib
import socket
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from butler import mailer, system, tick, update_report, updates
from fakes import fixtures
from fakes.home import Home
from test_updates import ApplySandbox

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
        self.assertIn("to-dos unavailable", problems)
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
        self.assertIn("Not connected yet: calendar, money, to-dos.", text)
        self.assertIn("City extends metro night service", text)

    def test_a_claude_timeout_reads_as_couldnt_reach_claude(self):
        self.assertEqual(self.problems(self.brief(self.record)["text"]),
                         "Problems this morning: news shown unedited (couldn't reach Claude).")

    def test_the_log_says_so_without_a_traceback(self):
        log = self.home.log()
        self.assertIn("calendar: the Calendar and Reminders helper isn't built yet", log)
        self.assertIn(f"money: no Lunch Money token in {system.PASSWORD_STORE}", log)
        self.assertNotIn("calendar failed", log)
        self.assertNotIn("money failed", log)


class WrongClaudeKeyTest(Case):
    """The butler's Claude key is wrong (a typo, or a key since deleted). The brief goes out with the
    feeds' own headlines, and the log says so in one plain line with the fix, every morning, not a
    traceback that buries it."""

    @classmethod
    def setUpClass(cls):
        cls.home = Home()
        cls.record = cls.home.run("daily", down=("claude-key",))

    def test_the_brief_says_claude_refused_the_key(self):
        text = self.brief(self.record)["text"]
        self.assertIn("City extends metro night service", text)
        problems = self.problems(text)
        self.assertIn("news shown unedited (Claude didn't accept your butler's API key)", problems)
        self.assertIn("couldn't flag events to prepare for (Claude didn't accept your butler's API key)", problems)

    def test_the_log_says_so_in_one_line_with_the_fix(self):
        log = self.home.log()
        self.assertIn("news selection failed: Claude didn't accept your butler's API key. "
                      "Store a new one with: butler secret butler-anthropic", log)
        self.assertIn("prep failed: Claude didn't accept your butler's API key.", log)
        self.assertNotIn("Traceback", log)
        self.assertNotIn("invalid x-api-key", log)
        self.assert_no_secrets()

    def test_butler_doctor_says_what_to_do(self):
        from butler import doctor
        (what, fix), = doctor.log_problems(self.home.log().splitlines())
        self.assertIn("Claude refused your butler's API key", what)
        self.assertIn("butler-anthropic", fix)


class ClaudeTroubleTest(unittest.TestCase):
    """Each way Claude says no, in the words the brief and the log use. Anything else is a bug."""

    def error(self, cls, message: str, status: int | None = None):
        """As the SDK raises it (the fakes build theirs the same way), without a real response."""
        error = cls.__new__(cls)
        Exception.__init__(error, message)
        if status:
            error.status_code = status
        return error

    def test_each_answer(self):
        import anthropic
        from butler import llm
        cases = [
            (self.error(anthropic.AuthenticationError, "invalid x-api-key", 401), "Claude didn't accept your butler's API key"),
            (self.error(anthropic.BadRequestError, "Your credit balance is too low to access the Anthropic API.", 400),
             "Claude's account is out of credit"),
            (self.error(anthropic.InternalServerError, "Overloaded", 529), "Claude API error 529"),
            (self.error(anthropic.APIConnectionError, "Connection error."), "couldn't reach Claude"),
            (KeyError("stories"), None),
        ]
        for e, words in cases:
            self.assertEqual(llm.trouble(e), words, type(e).__name__)

    def test_a_bug_still_logs_its_traceback(self):
        from butler import llm
        with self.assertLogs("butler", "ERROR") as logs:
            try:
                {}["stories"]
            except KeyError as e:
                llm.log_failure("news selection", e)
        self.assertIn("Traceback", "\n".join(logs.output))


class ClaudeNotConnectedYetTest(Case):
    """Before Module 4 stores the butler's Claude key, the brief lists Claude as not connected yet
    (lesson 4.5 says the note stops listing it), with the feeds' own headlines, and no problem."""

    @classmethod
    def setUpClass(cls):
        cls.home = Home(ek=False, secrets=("butler-gmail",))
        cls.record = cls.home.run("daily")

    def test_listed_quietly_with_the_headlines(self):
        text = self.brief(self.record)["text"]
        self.assertIn("Not connected yet: calendar, Claude, money, to-dos.", text)
        self.assertIn("City extends metro night service", text)
        self.assertNotIn("Problems this morning", text)

    def test_the_log_says_so_without_a_traceback(self):
        log = self.home.log()
        self.assertIn("news selection: Claude isn't connected yet", log)
        self.assertNotIn("news selection failed", log)
        self.assertNotIn("Traceback", log)


class HelperShippedButNoCalendarsYetTest(Case):
    """A release ships the Calendar helper prebuilt, so from the first brief (Module 0) it's in
    bin/. Until Module 3 picks calendars, the brief must not run it: macOS would ask for Calendar
    and Reminders access then, before any lesson says why (a persona test clicked Don't Allow)."""

    @classmethod
    def setUpClass(cls):
        import yaml
        cls.home = Home()
        path = cls.home.root / "config" / "settings.yaml"
        settings = yaml.safe_load(path.read_text())
        settings["calendar"]["include"] = []   # as setup_settings.fresh() writes it
        path.write_text(yaml.safe_dump(settings, sort_keys=False, allow_unicode=True))
        cls.record = cls.home.run("daily")

    def test_the_helper_never_runs(self):
        self.assertEqual(self.home.ek_calls(), [])

    def test_the_brief_says_not_connected_yet(self):
        self.assertIn("Not connected yet: calendar, to-dos.", self.brief(self.record)["text"])
        self.assertIn("no calendars are chosen yet", self.home.log())


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


class ReplyWhenClaudeKeepsFailingTest(Case):
    """A trusted reply whose Claude call keeps failing: three calls, then one note to everyone, then
    silence. Before, it went to Claude every five minutes with no end."""

    @classmethod
    def setUpClass(cls):
        cls.home = h = Home()
        h.add_reply("sam@example.com", "Re: Bento · Saturday", "add milk", "example.com")
        cls.runs = [h.run("replies", down=("claude",)) for _ in range(4)] + [h.run("replies")]

    def test_three_claude_calls_at_most(self):
        calls = [len([p for p in r["prompts"] if p["schema"] == "actions"]) for r in self.runs]
        self.assertEqual(calls, [1, 1, 1, 0, 0])

    def test_the_household_hears_once(self):
        mails = [m for r in self.runs for m in r["mails"]]
        self.assertEqual(len(mails), 1, [m["subject"] for m in mails])
        self.assertIn("I couldn't handle Sam's reply after 3 tries", mails[0]["text"])
        self.assertEqual(mails[0]["to"], ["sam@example.com", "alex@example.com"])
        self.assertIn("1", self.runs[3]["imap_seen"], "marked read once given up on")


class ReplyWhenGmailRefusesTest(Case):
    """Gmail refuses the confirmation: the actions happen once, the confirmation waits in the outbox,
    and the next tick sends it without asking Claude again."""

    @classmethod
    def setUpClass(cls):
        cls.home = h = Home()
        h.add_reply("sam@example.com", "Re: Bento · Saturday", "add milk", "example.com")
        h.set_actions({"Re: Bento · Saturday": [{"type": "add_shopping", "text": "milk"}]})

        def nothing_due(settings):
            for hour in ("send_hour", "weekly_hour", "monthly_hour"):
                settings["email"][hour] = 24
        h.edit_settings(nothing_due)
        cls.first = h.run("replies", down=("smtp",))
        cls.again = h.run("tick")

    def test_the_actions_happen_once(self):
        self.assertIsNone(self.first["error"], self.first["error"])
        self.assertEqual([p["schema"] for p in self.first["prompts"]], ["actions"])
        self.assertEqual(self.again["prompts"], [])
        adds = [c for c in self.home.ek_calls() if c["command"] == "add-reminder"]
        self.assertEqual(len(adds), 1)

    def test_the_confirmation_goes_out_on_the_next_tick(self):
        self.assertEqual(self.first["mails"], [])
        self.assertEqual(len(self.again["mails"]), 1)
        self.assertIn("Added to Shopping: milk", self.again["mails"][0]["text"])
        self.assertEqual(self.again["mails"][0]["in_reply_to"], "<reply-1@example.com>")


class OutboxBackoffTest(unittest.TestCase):
    """While Gmail keeps refusing, the outbox is tried on the next two ticks, then once an hour, and
    new mail waits with it. Nothing waits more than three days. The clock is simulated."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        # The simulated clock runs twelve days ahead of the file system's, as under the dates job's
        # libfaketime: the outbox must keep time on one clock, not mix in file times.
        self.start = time.time() + 12 * 24 * 60 * 60
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

    def test_the_clock_behind_the_file_system_changes_nothing(self):
        self.start = self.clock[0] = time.time() - 200 * 24 * 60 * 60
        self.test_fast_tries_then_hourly_then_everything_goes()

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
            (root / "config").mkdir()
            (root / "config" / "settings.yaml").write_text("name: Bento\n")
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


class UpdateBriefCheckTest(unittest.TestCase):
    """An update's health check builds today's brief with the new code (daily.brief_check). A
    release that crashes at run time fails it; a service that's down doesn't, and the check sends
    and writes nothing."""

    @classmethod
    def setUpClass(cls):
        cls.home = Home()

    @classmethod
    def tearDownClass(cls):
        cls.home.close()

    def check(self, **kwargs) -> list[dict]:
        record = self.home.run("brief-check", **kwargs)
        self.assertIsNone(record["error"], record["error"])
        self.assertEqual(record["mails"], [], "the check sends nothing")
        self.assertEqual(record["prompts"], [], "the check never calls Claude")
        return record["check"]["failures"]

    def test_a_healthy_release_passes_and_writes_nothing(self):
        before = sorted(p.relative_to(self.home.root) for p in (self.home.root / "out").rglob("*"))
        self.assertEqual(self.check(), [])
        self.assertEqual(sorted(p.relative_to(self.home.root) for p in (self.home.root / "out").rglob("*")), before)
        self.assertFalse((self.home.root / "data" / "networth.jsonl").exists(), "no net worth snapshot")

    def test_services_down_are_not_the_releases_fault(self):
        self.assertEqual(self.check(down=("lunchmoney", "weather", "news", "calendar")), [])

    def test_a_section_that_crashes_on_code_fails_it(self):
        failures = self.check(env={"BUTLER_FAKE_BUG": "money"})
        self.assertEqual([(f["where"], f["error"]) for f in failures], [("money failed", "TypeError")])
        self.assertIn("NoneType", failures[0]["detail"])

    def test_a_brief_that_cant_render_fails_it(self):
        failures = self.check(env={"BUTLER_FAKE_BUG": "render"})
        self.assertEqual([(f["where"], f["error"]) for f in failures], [("the brief", "AttributeError")])


class UpdateReportIsolationTest(ApplySandbox):
    """The outcome report (update_report.py) is a nicety. Whatever happens to it, the update
    succeeds, is recorded for the brief, and the log names no detail of what went wrong."""

    def update(self):
        with mock.patch.object(updates, "post_install", return_value={"ok": True}), \
                self.assertLogs("butler", "INFO") as logs:
            result = updates.apply(self.entry)
        self.assertEqual(result["status"], "updated")
        self.assertEqual(updates.installed_version(), "v0.2.0")
        self.assertEqual(updates.load_state()["last_result"], result, "recorded for the next brief")
        self.assertNotIn("boom", "\n".join(logs.output))
        return logs.output

    def test_a_send_that_raises_anything(self):
        with mock.patch.object(update_report.account, "send_update_outcome", side_effect=RuntimeError("boom")):
            self.update()

    def test_a_report_that_cant_be_written(self):
        with mock.patch.object(update_report, "_save", side_effect=PermissionError("boom")):
            self.update()

    def test_a_reporter_with_a_bug(self):
        with mock.patch.object(update_report, "record", side_effect=RuntimeError("boom")):
            self.update()

    def test_a_website_that_never_answers(self):
        quiet = socket.socket()
        quiet.bind(("127.0.0.1", 0))
        quiet.listen(1)                 # connects, then never says a word
        self.addCleanup(quiet.close)
        started = time.monotonic()
        with mock.patch.object(update_report.account, "API", f"http://127.0.0.1:{quiet.getsockname()[1]}/api"), \
                mock.patch.object(update_report, "TIMEOUT", 0.5):
            self.update()
        self.assertLess(time.monotonic() - started, 5, "the update waits a short timeout at most")
        self.assertEqual(len(update_report.load(updates.UPDATES / updates.REPORTS)["queued"]), 1, "it waits for the tick")

    def test_a_tick_whose_report_fails_still_checks_for_updates(self):
        with mock.patch.object(update_report, "_send", side_effect=RuntimeError("boom")), \
                mock.patch.object(updates, "fetch_manifest", return_value={"available": False}) as fetch:
            (updates.UPDATES / updates.REPORTS).parent.mkdir(parents=True, exist_ok=True)
            (updates.UPDATES / updates.REPORTS).write_text('{"queued": [{"at": "2026-10-12T03:00:00Z"}]}')
            updates.tick({"updates": "auto", "update_hour": 3}, datetime(2026, 10, 12, 3, 0))
        fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
