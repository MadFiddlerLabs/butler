"""Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from casa import projects

FIXTURE = """name: Test
summary: A test project.
steps:
  # comment that must survive edits
  - id: card
    do: Collect the health card
    short: "health card"
    who: Alex

  - id: gp
    do: Meet the doctor
    who: Alex
    after: [card]

  - id: eye
    do: Eye appointment
    who: Alex
    after: [gp]

  - id: check
    do: Check the tracker
    who: Both
    every: 30

  - id: file
    do: File the application
    who: Sam
    not_before: 2027-01-30

  - id: practice
    do: Practice tests
    who: Alex
    every: 1
    until: card

  - id: wait
    do: Hear back from the attorney
    who: Sam
    status: waiting
    chase_after: 2026-12-01
"""


class ProjectsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "test.yaml").write_text(FIXTURE)
        self._orig = projects.PROJECTS_DIR
        projects.PROJECTS_DIR = self.dir

    def tearDown(self):
        projects.PROJECTS_DIR = self._orig
        shutil.rmtree(self.dir)

    def steps(self):
        return {s.id: s for p in projects.load() for s in p.steps}

    def test_states(self):
        st, today = self.steps(), date(2026, 10, 8)
        group = {k: projects.state(s, st, today)[0] for k, s in st.items()}
        self.assertEqual(group, {"card": "now", "gp": "later", "eye": "later", "check": "now",
                                 "file": "later", "wait": "waiting", "practice": "now"})
        self.assertEqual(projects.state(st["gp"], st, today)[1], "needs: health card")
        self.assertEqual(projects.state(st["wait"], st, date(2026, 12, 1))[0], "now")

    def test_unlocks_in_waves(self):
        waves = projects.unlocks("card", self.steps())
        self.assertEqual([[s.id for s in w] for w in waves], [["gp"], ["eye"]])

    def test_today_view(self):
        v = projects.today_view(projects.load(), date(2026, 10, 8))
        self.assertEqual([s.id for s in v.actionable], ["card"])
        self.assertEqual([s.id for s in v.checks_due], ["check", "practice"])
        self.assertEqual([(s.id, d) for s, d in v.countdowns], [("file", 114)])

    def test_update_keeps_comments_and_unblocks(self):
        projects.update_step("card", status="done", last_done=date(2026, 10, 8))
        text = (self.dir / "test.yaml").read_text()
        self.assertIn("# comment that must survive edits", text)
        self.assertIn("    status: done\n", text)
        v = projects.today_view(projects.load(), date(2026, 10, 8))
        self.assertIn("gp", [s.id for s in v.actionable])

    def test_until_retires_a_habit(self):
        projects.update_step("card", status="done")
        st = self.steps()
        self.assertEqual(projects.state(st["practice"], st, date(2026, 10, 8))[0], "done")
        v = projects.today_view(projects.load(), date(2026, 10, 8))
        self.assertNotIn("practice", [s.id for s in v.checks_due])

    def test_waiting_fields(self):
        projects.update_step("eye", status="waiting", waiting_on='clinic "Example"',
                             chase_after=date(2026, 11, 1))
        s = self.steps()["eye"]
        self.assertEqual((s.status, s.waiting_on, s.chase_after), ("waiting", 'clinic "Example"', date(2026, 11, 1)))

    def test_unknown_step(self):
        with self.assertRaises(KeyError):
            projects.update_step("nope", status="done")


if __name__ == "__main__":
    unittest.main()
