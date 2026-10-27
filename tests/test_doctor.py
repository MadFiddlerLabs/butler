"""`butler doctor`'s checks on what the Mac records: log lines and restarts (src/butler/doctor.py).
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from butler import doctor

LOG = [
    "2026-10-12 07:05:01,120 INFO replies: ignored mail from sam@example.com (DKIM signature missing or failed)",
    "2026-10-12 07:10:01,120 INFO replies: ignored mail from alex@example.com (DKIM signature missing or failed)",
    "2026-10-12 07:15:02,004 WARNING send failed, queued brief-2026-10-12 for a retry "
    "((535, b'5.7.8 Username and Password not accepted. For more information, go to'))",
]
# Invented `last -t console sam` output, newest first.
LAST = """sam       console                   Tue Oct 13 09:40   still logged in
sam       console                   Mon Oct 12 21:41 - 03:02  (05:21)
sam       console                   Tue Oct 14 08:00 - 09:00  (01:00)
sam       console                   Tue Sep 15 17:08 - 12:49 (20+19:41)

wtmp begins Tue Sep 15 17:08
"""


class LogTest(unittest.TestCase):
    def test_dkim_failures_and_a_refused_gmail_password(self):
        with mock.patch.object(doctor.system, "NAME", "mac"):
            (dkim, dkim_fix), (gmail, gmail_fix) = doctor.log_problems(LOG)
        self.assertIn("ignored 2 recent replies", dkim)
        self.assertIn("personal address", dkim_fix)
        self.assertNotIn("example.com", dkim + dkim_fix, "no sender is named")
        self.assertIn("Gmail refused", gmail)
        self.assertIn("Changing the Gmail password", gmail_fix)
        self.assertIn("store-secret.sh butler-gmail", gmail_fix)

    def test_on_windows_the_gmail_fix_is_butler_secret(self):
        """store-secret.sh is a Mac dialog. Windows stores the password with `butler secret`."""
        with mock.patch.object(doctor.system, "NAME", "windows"):
            (_, gmail_fix), = [p for p in doctor.log_problems(LOG) if "Gmail" in p[0]]
        self.assertIn("type butler secret butler-gmail in a new Terminal window", gmail_fix)
        self.assertNotIn("store-secret.sh", gmail_fix)

    def test_imap_says_it_differently(self):
        problems = doctor.log_problems(["ERROR replies failed", "imaplib.IMAP4.error: b'[AUTHENTICATIONFAILED] Invalid"])
        self.assertEqual(len(problems), 1)
        self.assertIn("Gmail refused", problems[0][0])

    def test_a_quiet_log(self):
        self.assertEqual(doctor.log_problems(["2026-10-12 07:00:01,000 INFO outbox: sent brief.eml"]), [])

    def test_the_reason_matches_what_the_inbox_logs(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "butler" / "inbox.py").read_text()
        self.assertIn(f'"{doctor.DKIM}"', source)


class RestartTest(unittest.TestCase):
    def test_boot_time(self):
        boot = doctor.boot_time("{ sec = 1791875552, usec = 594818 } Tue Oct 13 09:12:32 2026\n")
        self.assertEqual(boot, datetime.fromtimestamp(1791875552))
        self.assertIsNone(doctor.boot_time("sysctl: unknown oid 'kern.boottime'"))

    def test_the_first_login_after_the_restart(self):
        boot = datetime(2026, 10, 13, 3, 2, 40)
        now = datetime(2026, 10, 13, 11, 0)
        # Sessions before the restart, and records outside the window (an older year's), don't count.
        self.assertEqual(doctor.first_login(LAST, boot, now), datetime(2026, 10, 13, 9, 40))
        self.assertIsNone(doctor.first_login("", boot, now))

    def test_a_login_in_the_same_minute_as_the_boot(self):
        boot = datetime(2026, 10, 13, 9, 40, 30)
        self.assertEqual(doctor.first_login(LAST, boot, datetime(2026, 10, 13, 11, 0)),
                         datetime(2026, 10, 13, 9, 40))

    def test_a_january_login_after_a_december_restart(self):
        last = "sam  console  Fri Jan  1 08:30   still logged in\n"
        boot = datetime(2026, 12, 31, 23, 50)
        self.assertEqual(doctor.first_login(last, boot, datetime(2027, 1, 1, 9, 0)), datetime(2027, 1, 1, 8, 30))

    def test_a_long_wait_at_the_login_screen_is_reported(self):
        (what, fix), = doctor.restart_problems(datetime(2026, 10, 13, 3, 2), datetime(2026, 10, 13, 9, 40))
        self.assertIn("restarted on Tue 13 Oct at 03:02 and waited at the login screen until 09:40", what)
        self.assertIn("log back in", fix)
        (what, _), = doctor.restart_problems(datetime(2026, 10, 12, 23, 0), datetime(2026, 10, 13, 7, 30))
        self.assertIn("until Tue 13 Oct at 07:30", what)

    def test_a_quick_login_or_nothing_known_is_fine(self):
        self.assertEqual(doctor.restart_problems(datetime(2026, 10, 13, 9, 12), datetime(2026, 10, 13, 9, 13)), [])
        self.assertEqual(doctor.restart_problems(None, None), [])
        self.assertEqual(doctor.restart_problems(datetime(2026, 10, 13, 9, 12), None), [])

    def test_without_the_tools_it_says_nothing(self):
        with mock.patch.object(doctor.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(doctor.restart_check("sam"), [])

    def test_the_live_check_reads_both_tools(self):
        answers = {"sysctl": "{ sec = %d, usec = 0 }" % datetime(2026, 10, 13, 3, 2).timestamp(), "last": LAST}

        def run(cmd, **kwargs):
            if cmd[0] == "last":
                self.assertEqual(kwargs["env"]["LC_ALL"], "C", "English month names")
            return mock.Mock(stdout=answers[cmd[0]])

        with mock.patch.object(doctor.subprocess, "run", side_effect=run):
            (what, _), = doctor.restart_check("sam", now=datetime(2026, 10, 13, 11, 0))
        self.assertIn("until 09:40", what)


if __name__ == "__main__":
    unittest.main()
