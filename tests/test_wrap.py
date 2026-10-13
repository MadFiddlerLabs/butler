"""Setup text fits a new 80-column Terminal window, and a `butler <command>` in a sentence is never
split across lines (people copy it). Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import re
import unittest

from fakes.account_api import FakeAccountAPI
from fakes.setup_home import SetupHome

ANSI = re.compile(r"\x1b\[[0-9;]*m")
LESSONS = ['butler town "Lisbon, Portugal"', "butler adults Sam Alex", "butler brief", "butler claude", ""]


class WrapTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeAccountAPI(auto_verify=True).start()
        self.addCleanup(self.api.stop)
        self.home = SetupHome(self.api, butler_name="Bento")
        self.addCleanup(self.home.close)
        self.home.env["BUTLER_COLUMNS"] = "80"

    def lines(self, text: str) -> list[str]:
        """Printed lines as a student sees them. A prompt (\u201cContinue? [Y/n] \u201d, \u201c\u203a \u201d) waits on its line for
        the student's Enter; captured output has no Enter, so the next line follows on. Split it."""
        out = []
        for line in text.splitlines():
            out += re.split(r"(?<=\[Y/n\] )|(?<=\u203a )|(?<=type no: )", ANSI.sub("", line))
        return out

    def test_first_run_fits_80_columns(self):
        proc = self.home.first_run(["", "", "", "sam@example.com", "", *LESSONS])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for line in self.lines(proc.stdout):
            if " " not in line.strip() or line.startswith("(dry run)"):   # a URL or path stays whole
                continue
            self.assertLessEqual(len(line), 80, repr(line))

    def test_commands_in_sentences_stay_whole(self):
        proc = self.home.first_run(["", "", "", "skip", *LESSONS[:3], ""])
        for line in self.lines(proc.stdout):
            self.assertFalse(line.rstrip().endswith(" butler") or line.rstrip() == "butler",
                             f"a command was split after 'butler': {line!r}")

    def test_cli_text_fits_80_columns(self):
        self.home.bootstrap()
        proc = self.home.butler("claude", "--yes")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for line in self.lines(proc.stdout):
            if " " not in line.strip() or line.startswith("(dry run)"):
                continue
            self.assertLessEqual(len(line), 80, repr(line))


class SayTest(unittest.TestCase):
    """cli.say on its own, in a narrow window."""

    def say(self, text: str, columns: str = "50") -> str:
        import contextlib
        import io
        import os
        from unittest import mock
        from butler import cli
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"BUTLER_COLUMNS": columns}), contextlib.redirect_stdout(out):
            cli.say(text)
        return out.getvalue()

    def test_a_quoted_command_stays_whole(self):
        out = self.say('Not the right Lisbon? Add the country, like this: butler town "Lisbon, Portugal"')
        self.assertIn('butler town "Lisbon, Portugal"', out)
        self.assertTrue(all(len(l) <= 46 or "butler" in l for l in out.splitlines()), out)

    def test_indented_commands_are_never_wrapped(self):
        line = "    butler town \"Lisbon, Portugal\"   and some more words to make it long enough"
        self.assertEqual(self.say(line, "30"), line + "\n")


if __name__ == "__main__":
    unittest.main()
