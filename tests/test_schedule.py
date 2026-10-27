"""Butler's Windows scheduled tasks (butler.schedule) and run.py, the Windows entry point. The
tasks' XML is checked everywhere; installing them in Task Scheduler runs only on Windows
(tests.yml's windows job), in a throwaway folder.

Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""
import secrets
import shutil
import string
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from butler import schedule

REPO = Path(__file__).resolve().parents[1]
NS = {"t": schedule.NAMESPACE}
ROOT = Path("C:/Users/Sam & Alex/Butler")   # invented, with a space and an & to escape


def parsed(send_hour=7, weekly_hour=17) -> dict[str, ET.Element]:
    return {name: ET.fromstring(xml.split("\n", 1)[1])
            for name, xml in schedule.tasks(ROOT, send_hour, weekly_hour).items()}


class TasksTest(unittest.TestCase):
    def test_each_launchd_job_has_its_task(self):
        self.assertEqual(set(parsed()), {"tick", "morning", "portal"})

    def test_the_tick_runs_every_five_minutes_and_at_logon(self):
        tick = parsed()["tick"]
        self.assertEqual(tick.find("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", NS).text, "PT5M")
        self.assertIsNotNone(tick.find("t:Triggers/t:LogonTrigger", NS))
        self.assertEqual(tick.find("t:Settings/t:WakeToRun", NS).text, "false")

    def test_the_morning_task_wakes_the_computer_a_minute_past_each_send_hour(self):
        morning = parsed(send_hour=7, weekly_hour=17)["morning"]
        starts = [t.text for t in morning.findall("t:Triggers/t:CalendarTrigger/t:StartBoundary", NS)]
        self.assertEqual([s[-8:] for s in starts], ["07:01:00", "17:01:00"])
        self.assertEqual(morning.find("t:Settings/t:WakeToRun", NS).text, "true")
        same = parsed(send_hour=8, weekly_hour=8)["morning"]
        self.assertEqual(len(same.findall("t:Triggers/t:CalendarTrigger", NS)), 1)

    def test_the_portal_keeps_running(self):
        portal = parsed()["portal"]
        self.assertEqual(portal.find("t:Settings/t:ExecutionTimeLimit", NS).text, "PT0S")
        self.assertIsNotNone(portal.find("t:Settings/t:RestartOnFailure", NS))
        self.assertIn(" portal", portal.find("t:Actions/t:Exec/t:Arguments", NS).text)

    def test_every_task_runs_run_py_as_the_household_without_admin_rights(self):
        for name, task in parsed().items():
            principal = task.find("t:Principals/t:Principal", NS)
            self.assertEqual(principal.find("t:LogonType", NS).text, "InteractiveToken", name)
            self.assertEqual(principal.find("t:RunLevel", NS).text, "LeastPrivilege", name)
            run = task.find("t:Actions/t:Exec", NS)
            self.assertEqual(Path(run.find("t:Command", NS).text), ROOT / ".venv" / "Scripts" / "pythonw.exe")
            self.assertTrue(run.find("t:Arguments", NS).text.startswith(f'-X utf8 "{ROOT / "run.py"}" '), name)
            self.assertEqual(Path(run.find("t:WorkingDirectory", NS).text), ROOT)
            self.assertEqual(task.find("t:Settings/t:StartWhenAvailable", NS).text, "true", name)
            self.assertEqual(task.find("t:Settings/t:DisallowStartIfOnBatteries", NS).text, "false", name)


class RunPyTest(unittest.TestCase):
    """run.py from a copy of the repo, with a module that reports how it was run."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        shutil.copy(REPO / "run.py", self.root / "run.py")
        shutil.copytree(REPO / "src", self.root / "src", ignore=shutil.ignore_patterns("__pycache__"))
        (self.root / "src" / "butler" / "probe.py").write_text(
            "import os, sys\nprint(sys.flags.utf8_mode, os.getcwd(), sys.argv[1:])\nsys.exit(3 if sys.argv[1:] == ['fail'] else 0)\n")

    def run_py(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(self.root / "run.py"), *args], capture_output=True, text=True,
                              cwd=cwd or tempfile.gettempdir(), timeout=60)

    def test_it_runs_the_module_in_utf8_mode_from_the_butler_folder(self):
        out = self.run_py("probe", "--dry-run")
        self.assertEqual(out.returncode, 0, out.stderr)
        mode, cwd, args = out.stdout.strip().split(" ", 2)
        self.assertEqual(mode, "1")
        self.assertEqual(Path(cwd).resolve(), self.root.resolve())
        self.assertEqual(args, "['--dry-run']")

    def test_the_exit_code_comes_back(self):
        self.assertEqual(self.run_py("probe", "fail").returncode, 3)


@unittest.skipUnless(sys.platform == "win32", "Task Scheduler is Windows only")
class TaskSchedulerTest(unittest.TestCase):
    def setUp(self):
        self.folder = "\\ButlerTest" + "".join(secrets.choice(string.ascii_lowercase) for _ in range(8)) + "\\"
        self.addCleanup(schedule.uninstall, self.folder)

    def query(self, name: str) -> subprocess.CompletedProcess:
        return subprocess.run(["schtasks", "/Query", "/TN", self.folder + name, "/XML"], capture_output=True, text=True)

    def test_install_creates_each_task_and_uninstall_removes_them(self):
        names = schedule.install(REPO, 7, 17, folder=self.folder)
        self.assertEqual(names, [self.folder + n for n in ("tick", "morning", "portal")])
        for name in ("tick", "morning", "portal"):
            self.assertEqual(self.query(name).returncode, 0, name)
        self.assertIn("<WakeToRun>true</WakeToRun>", self.query("morning").stdout)
        schedule.install(REPO, 8, 17, folder=self.folder)      # again: replaced, not doubled
        self.assertIn("T08:01:00", self.query("morning").stdout)
        self.assertEqual(sorted(schedule.uninstall(self.folder)), sorted(names))
        for name in ("tick", "morning", "portal"):
            self.assertNotEqual(self.query(name).returncode, 0, name)


if __name__ == "__main__":
    unittest.main()
