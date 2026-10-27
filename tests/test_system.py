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
from unittest import mock

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
        self.assertEqual(system.PASSWORD_STORE, "Credential Manager" if sys.platform == "win32" else "Keychain")
        for platform in ("mac.py", "windows.py"):
            self.assertIn("\nPASSWORD_STORE = ", (SYSTEM / platform).read_text(), platform)


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


@unittest.skipIf(sys.platform == "win32", "system/mac.py needs fcntl")
class MacPathAndAppTest(unittest.TestCase):
    """The PATH line and the Butler app, as scripts/lib/install.sh finds them (the first run's
    equivalence test compares the two)."""

    def setUp(self):
        from butler.system import mac
        self.mac = mac
        self.home = Path(tempfile.mkdtemp())
        self.apps = Path(tempfile.mkdtemp())
        self.enterContext(mock.patch.object(mac.Path, "home", return_value=self.home))
        self.enterContext(mock.patch.dict("os.environ", {"BUTLER_SYSTEM_APPS": str(self.apps)}))

    def test_the_profile_line_is_added_once(self):
        self.assertTrue(self.mac.add_to_path("/Users/sam/Butler/bin"))
        self.assertFalse(self.mac.add_to_path("/Users/sam/Butler/bin"))
        self.assertEqual((self.home / ".zprofile").read_text(),
                         '\n# Added by Butler: lets you type `butler` in Terminal.\nexport PATH="/Users/sam/Butler/bin:$PATH"\n')

    def test_a_profile_that_cant_be_written_is_no_change(self):
        (self.home / ".zprofile").mkdir()
        self.assertFalse(self.mac.add_to_path("/Users/sam/Butler/bin"))

    def plist(self, bundle_id: str) -> None:
        contents = self.apps / "Butler.app" / "Contents"
        contents.mkdir(parents=True)
        (contents / "Info.plist").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict><key>CFBundleIdentifier</key>'
            f"<string>{bundle_id}</string></dict></plist>\n")

    def test_the_app_in_applications_counts_only_if_its_ours(self):
        self.assertIsNone(self.mac.butler_app())
        self.plist("com.example.another-butler")
        self.assertIsNone(self.mac.butler_app())
        (self.home / "Applications" / "Butler.app").mkdir(parents=True)
        self.assertEqual(self.mac.butler_app(), str(self.home / "Applications" / "Butler.app"))
        (self.apps / "Butler.app" / "Contents" / "Info.plist").write_text(f"<plist>{self.mac.APP_ID}")
        self.assertEqual(self.mac.butler_app(), str(self.home / "Applications" / "Butler.app"), "a broken plist")

    def test_the_app_in_applications_comes_first(self):
        (self.home / "Applications" / "Butler.app").mkdir(parents=True)
        self.plist(self.mac.APP_ID)
        self.assertEqual(self.mac.butler_app(), str(self.apps / "Butler.app"))


@unittest.skipUnless(sys.platform == "win32", "the user's PATH is in the Windows registry")
class WindowsPathTest(unittest.TestCase):
    """The user's PATH in the registry, in a throwaway key in place of HKCU\\Environment."""

    def setUp(self):
        import winreg
        from butler.system import windows
        self.winreg, self.windows = winreg, windows
        self.parent = "Software\\ButlerTest" + "".join(secrets.choice(string.ascii_lowercase) for _ in range(10))
        self.key = self.parent + "\\Environment"
        self.enterContext(mock.patch.object(windows, "ENVIRONMENT", self.key))
        self.addCleanup(self._delete)

    def _delete(self):
        for key in (self.key, self.parent):
            try:
                self.winreg.DeleteKey(self.winreg.HKEY_CURRENT_USER, key)
            except OSError:
                pass

    def path(self) -> tuple[str, int]:
        with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, self.key) as key:
            return self.winreg.QueryValueEx(key, "Path")

    def test_added_once_after_whats_there(self):
        with self.winreg.CreateKey(self.winreg.HKEY_CURRENT_USER, self.key) as key:
            self.winreg.SetValueEx(key, "Path", 0, self.winreg.REG_EXPAND_SZ, "%USERPROFILE%\\tools;C:\\Tools;")
        self.assertTrue(self.windows.add_to_path("C:\\Users\\sam\\Butler\\bin"))
        self.assertFalse(self.windows.add_to_path("c:\\users\\sam\\butler\\bin\\"), "the same folder")
        self.assertEqual(self.path(), ("%USERPROFILE%\\tools;C:\\Tools;C:\\Users\\sam\\Butler\\bin",
                                       self.winreg.REG_EXPAND_SZ))

    def test_a_first_path(self):
        self.assertTrue(self.windows.add_to_path("C:\\Users\\sam\\Butler\\bin"))
        self.assertEqual(self.path(), ("C:\\Users\\sam\\Butler\\bin", self.winreg.REG_EXPAND_SZ))

    def test_there_is_no_butler_app_yet(self):
        self.assertIsNone(self.windows.butler_app())


if __name__ == "__main__":
    unittest.main()
