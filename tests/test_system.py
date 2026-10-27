"""butler.system: the same functions on macOS and Windows, and each platform's own checks. The
Windows tests use the real Credential Manager, so they run only on Windows (tests.yml's windows
job); the Mac's Keychain is faked everywhere else (tests/fakes).

Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""
import ast
import secrets
import string
import sys
import tempfile
import unittest
from pathlib import Path

from butler import system

SYSTEM = Path(system.__file__).parent


def _functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")}


class SameEverywhereTest(unittest.TestCase):
    def test_both_platforms_have_every_function(self):
        wanted = _functions(SYSTEM / "__init__.py")
        for platform in ("mac.py", "windows.py"):
            self.assertLessEqual(wanted, _functions(SYSTEM / platform), platform)

    def test_nothing_outside_system_calls_the_mac_tools(self):
        """Keychain, launchd, VoiceOver and `open` belong to system/mac.py, so a Windows port
        only has to change system/. The Calendar helper and the Mac updater are their own work
        (#40)."""
        src = SYSTEM.parent
        for path in src.glob("*.py"):
            text = path.read_text()
            for tool in ('"security"', '"launchctl", "list"', '"pgrep", "-x", "VoiceOver"', '["open",', "import fcntl"):
                self.assertNotIn(tool, text, f"{path.name} calls {tool}: use butler.system")

    def test_dates_never_ask_strftime_for_a_day_without_its_zero(self):
        """Windows' strftime refuses %-d, so it goes through fmt.strftime, which fills it in."""
        from datetime import date
        from butler.fmt import strftime
        self.assertEqual(strftime(date(2026, 10, 3), "%a %-d %b %Y"), "Sat 3 Oct 2026")
        self.assertEqual(strftime(date(2026, 10, 13), "%-d %B"), "13 October")
        for path in SYSTEM.parent.glob("*.py"):
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if "%-d" in line and "strftime(" not in line and path.name != "fmt.py":
                    self.fail(f"{path.name}:{n} formats %-d without fmt.strftime")

    def test_this_computer_gets_its_own(self):
        self.assertEqual(system.NAME, "windows" if sys.platform == "win32" else "mac")


class LockTest(unittest.TestCase):
    def test_a_second_lock_fails_at_once(self):
        tmp = Path(tempfile.mkdtemp())
        with (tmp / "lock").open("w") as first, (tmp / "lock").open("w") as second:
            system.lock_exclusive(first)
            with self.assertRaises(OSError):
                system.lock_exclusive(second)
        with (tmp / "lock").open("w") as again:
            system.lock_exclusive(again)


@unittest.skipUnless(sys.platform == "win32", "Credential Manager is Windows only")
class CredentialManagerTest(unittest.TestCase):
    def setUp(self):
        self.service = "butler-test" + "".join(secrets.choice(string.ascii_lowercase) for _ in range(10))
        self.addCleanup(self._delete)

    def _delete(self):
        import ctypes
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi32.CredDeleteW(self.service, 1, 0)

    def test_a_secret_round_trips_and_is_replaced(self):
        self.assertFalse(system.has_secret(self.service))
        self.assertIsNone(system.get_secret(self.service))
        secret = 'inv"ent\\ed $x  0000000000000000000000000000000 é'    # invented
        system.store_secret(self.service, secret)
        self.assertTrue(system.has_secret(self.service))
        self.assertEqual(system.get_secret(self.service), secret)
        system.store_secret(self.service, "invented-second")
        self.assertEqual(system.get_secret(self.service), "invented-second")

    def test_a_secret_too_long_for_windows_is_refused_before_saving(self):
        with self.assertRaises(ValueError):
            system.store_secret(self.service, "x" * 3000)
        self.assertFalse(system.has_secret(self.service))

    def test_no_task_means_not_scheduled(self):
        self.assertFalse(system.scheduled())

    def test_the_screen_reader_check_answers(self):
        self.assertIsInstance(system.screen_reader_on(), bool)


if __name__ == "__main__":
    unittest.main()
