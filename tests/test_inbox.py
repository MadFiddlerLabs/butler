"""Guards the reply trust check: only Gmail's own DKIM verdict for the sender's exact domain counts.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import email
import unittest

from butler.inbox import _dkim_passes

GMAIL_PASS = ("Authentication-Results: mx.google.com;\n"
              "       dkim=pass header.i=@example.com header.s=s1 header.b=abc;\n"
              "       spf=pass smtp.mailfrom=sam@example.com;\n"
              "       dmarc=pass (p=NONE sp=NONE dis=NONE) header.from=example.com\n")


def message(headers: str) -> email.message.Message:
    return email.message_from_string(headers + "From: sam@example.com\nSubject: add milk\n\nadd milk\n")


class DkimTest(unittest.TestCase):
    def test_gmail_pass_for_the_sender_domain(self):
        self.assertTrue(_dkim_passes(message(GMAIL_PASS), "sam@example.com"))

    def test_header_d_form(self):
        msg = message("Authentication-Results: mx.google.com; dkim=pass header.d=example.com header.s=s1\n")
        self.assertTrue(_dkim_passes(msg, "sam@example.com"))

    def test_lookalike_domain_fails(self):
        msg = message("Authentication-Results: mx.google.com; dkim=pass header.i=@example.com.attacker.test\n")
        self.assertFalse(_dkim_passes(msg, "sam@example.com"))

    def test_other_domain_fails(self):
        msg = message("Authentication-Results: mx.google.com; dkim=pass header.i=@attacker.test\n")
        self.assertFalse(_dkim_passes(msg, "sam@example.com"))

    def test_failed_dkim_fails(self):
        msg = message("Authentication-Results: mx.google.com; dkim=fail header.i=@example.com\n")
        self.assertFalse(_dkim_passes(msg, "sam@example.com"))

    def test_forged_header_below_gmails_fails(self):
        forged = "Authentication-Results: mx.google.com; dkim=pass header.i=@example.com\n"
        gmail = "Authentication-Results: mx.google.com; dkim=none\n"
        self.assertFalse(_dkim_passes(message(gmail + forged), "sam@example.com"))

    def test_other_authserv_fails(self):
        msg = message("Authentication-Results: mail.attacker.test; dkim=pass header.i=@example.com\n")
        self.assertFalse(_dkim_passes(msg, "sam@example.com"))

    def test_no_header_fails(self):
        self.assertFalse(_dkim_passes(message(""), "sam@example.com"))


class StandaloneTest(unittest.TestCase):
    def test_replies_imports_on_its_own(self):
        """`./run.sh replies` starts a fresh Python, where nothing has imported email.message yet."""
        import subprocess
        import sys
        from pathlib import Path
        src = Path(__file__).resolve().parents[1] / "src"
        out = subprocess.run([sys.executable, "-c", "import butler.replies"], capture_output=True, text=True,
                             env={"PYTHONPATH": str(src), "PATH": "/usr/bin:/bin"})
        self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == "__main__":
    unittest.main()
