"""The account client against the fake API: magic-link sign-in, progress, and that nothing breaks
when the server is down.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import unittest
from unittest import mock

from butler import account
from fakes.account_api import FakeAccountAPI


class SignInTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeAccountAPI().start()
        self.addCleanup(self.api.stop)
        patcher = mock.patch.object(account, "API", self.api.url + "/api")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_start_poll_click_poll(self):
        r = account.start("sam@example.com", "Bento", "setup/test")
        self.assertEqual(r["expires_in"], 900)
        self.assertEqual(account.poll(r["request_id"], r["poll_secret"])["status"], "pending")
        self.api.click()
        verified = account.poll(r["request_id"], r["poll_secret"])
        self.assertEqual(verified["status"], "verified")
        self.assertTrue(verified["session_token"])
        # The session is handed out once.
        self.assertNotIn("session_token", account.poll(r["request_id"], r["poll_secret"]))

    def test_start_sends_the_recorded_terms(self):
        import tempfile
        from pathlib import Path
        terms = Path(tempfile.mkdtemp()) / "terms.json"
        terms.write_text('{"terms_version": "0.1-draft", "terms_accepted_at": "2026-10-10T18:00:00Z"}')
        with mock.patch.object(account, "TERMS_FILE", terms):
            r = account.start("sam@example.com", "Bento", "setup/test")
        sent = self.api.requests[r["request_id"]]
        self.assertEqual((sent["terms_version"], sent["terms_accepted_at"]), ("0.1-draft", "2026-10-10T18:00:00Z"))

    def test_start_without_terms_sends_none(self):
        from pathlib import Path
        with mock.patch.object(account, "TERMS_FILE", Path("/nonexistent/terms.json")):
            r = account.start("sam@example.com", "Bento", "setup/test")
        self.assertIsNone(self.api.requests[r["request_id"]]["terms_version"])

    def start_with_heard(self, recorded: str | None) -> dict:
        """The body /auth/start received, with data/heard.json holding `recorded` (None: no file)."""
        import tempfile
        from pathlib import Path
        heard = Path(tempfile.mkdtemp()) / "heard.json"
        if recorded is not None:
            heard.write_text(recorded)
        with mock.patch.object(account, "HEARD_FILE", heard):
            account.start("sam@example.com", "Bento", "setup/test")
        return self.api.calls[-1][2]

    def test_start_sends_where_they_heard(self):
        self.assertEqual(self.start_with_heard('{"heard": "hacker_news"}')["heard"], "hacker_news")

    def test_a_skip_or_no_answer_sends_no_heard_key(self):
        for recorded in ('{"heard": null}', None, "not json", "[]"):
            self.assertNotIn("heard", self.start_with_heard(recorded), recorded)

    def test_only_a_listed_source_is_sent(self):
        self.assertNotIn("heard", self.start_with_heard('{"heard": "my neighbour Pat"}'))

    def test_wrong_poll_secret(self):
        r = account.start("sam@example.com", "Bento", "setup/test")
        with self.assertRaises(account.AccountError):
            account.poll(r["request_id"], "wrong")

    def test_bad_email_error_is_readable(self):
        with self.assertRaisesRegex(account.AccountError, "doesn't look right"):
            account.start("not-an-email", "Bento", "setup/test")

    def test_expired(self):
        self.api.expire = True
        r = account.start("sam@example.com", "Bento", "setup/test")
        self.assertEqual(account.poll(r["request_id"], r["poll_secret"])["status"], "expired")

    def test_progress_with_a_session(self):
        r = account.start("sam@example.com", "Bento", "setup/test")
        self.api.click()
        token = account.poll(r["request_id"], r["poll_secret"])["session_token"]
        with mock.patch.object(account, "session", return_value=token):
            self.assertTrue(account.record("setup.town"))
            self.assertEqual(account.me()["email"], "sam@example.com")
        self.assertEqual(self.api.progress[-1]["lesson"], "setup.town")

    def test_progress_without_a_session_does_nothing(self):
        with mock.patch.object(account, "session", return_value=None):
            self.assertFalse(account.record("setup.town"))
        self.assertEqual(self.api.progress, [])

    def test_a_revoked_session_is_harmless(self):
        with mock.patch.object(account, "session", return_value="not-a-session"):
            self.assertFalse(account.record("setup.town"))
            self.assertIsNone(account.me())


class OfflineTest(unittest.TestCase):
    def test_nothing_raises_offline(self):
        with mock.patch.object(account, "API", "http://127.0.0.1:9/api"), \
             mock.patch.object(account, "session", return_value="token"):
            self.assertFalse(account.record("setup.town"))
            self.assertIsNone(account.me())
            with self.assertRaisesRegex(account.AccountError, "couldn't reach"):
                account.start("sam@example.com", "Bento", "setup/test")


if __name__ == "__main__":
    unittest.main()
