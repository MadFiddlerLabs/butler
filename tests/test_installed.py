"""butler.installed: what the Windows installer does after copying Butler (the Mac's postinstall,
for Windows). The installer itself is tested end to end in windows-e2e.yml.

Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from butler import installed


class NameTest(unittest.TestCase):
    def test_the_name_comes_from_the_installers_filename(self):
        for path, want in [
            (r"C:\Users\Sam\Downloads\Bento.exe", "Bento"),
            (r"C:\Users\Sam\Downloads\Bento (1).exe", "Bento"),
            (r"C:\Users\Sam\Downloads\Bento-2.EXE", "Bento"),
            (r"C:\Users\Sam\Downloads\Mrs%20Hudson.exe", "Mrs Hudson"),
            (r"C:\Users\Sam\Downloads\Zoë.exe", "Zoë"),
            ("Jeeves.exe", "Jeeves"),
        ]:
            self.assertEqual(installed.name_from_installer(path), want, path)

    def test_the_generic_download_and_odd_names_give_none(self):
        for path in ("Butler.exe", "Butler-Setup.exe", r"D:\dist\Butler-Setup.exe", "", "a;b.exe",
                     "x" * 30 + ".exe", "---.exe"):
            self.assertIsNone(installed.name_from_installer(path), path)


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "bin").mkdir()
        (self.root / "bin" / "butler.cmd").write_text("@echo off\n")
        (self.root / ".butler-version").write_text("v9.9.9\n")
        self.opened = []
        patch = mock.patch.object(installed, "open_console", side_effect=lambda root, args: self.opened.append(args))
        patch.start()
        self.addCleanup(patch.stop)

    def log(self) -> str:
        return (self.root / "logs" / "install.log").read_text(encoding="utf-8")

    def install(self, installer="Bento.exe", silent=False) -> None:
        with mock.patch.object(installed, "ROOT", self.root):
            self.assertEqual(installed.main(["--installer", installer, *(["--silent"] if silent else [])]), 0)

    def test_a_fresh_install_names_the_butler_and_opens_the_first_run(self):
        self.install()
        self.assertEqual((self.root / ".butler-name").read_text(encoding="utf-8"), "Bento\n")
        self.assertEqual(self.opened, [["setup"]])
        self.assertRegex(self.log(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ v9\.9\.9 installed \(fresh\): opened a console "
                                     r"with butler setup\n$")

    def test_over_a_started_butler_nothing_of_theirs_changes_and_it_offers_the_choice(self):
        (self.root / ".butler-name").write_text("Bento\n")
        (self.root / "config").mkdir()
        (self.root / "config" / "settings.yaml").write_text("name: Bento\n")
        self.install("Jeeves.exe")
        self.assertEqual((self.root / ".butler-name").read_text(), "Bento\n")
        self.assertEqual((self.root / "config" / "settings.yaml").read_text(), "name: Bento\n")
        self.assertEqual(self.opened, [["setup", "--again"]])
        self.assertIn("installed (started)", self.log())

    def test_a_finished_first_run_counts_as_finished(self):
        (self.root / "data").mkdir()
        (self.root / "data" / "first-run-done").write_text("2026-10-27T07:00:00Z\n")
        self.assertEqual(installed.install_state(self.root), "finished")
        shutil.rmtree(self.root / "data")
        (self.root / "config").mkdir()
        (self.root / "config" / "settings.yaml").write_text("name: Bento\n")
        (self.root / "out" / "preview").mkdir(parents=True)
        (self.root / "out" / "preview" / "brief-2026-10-27.html").write_text("<html></html>")
        self.assertEqual(installed.install_state(self.root), "finished")

    def test_a_silent_install_opens_nothing(self):
        self.install(silent=True)
        self.assertEqual(self.opened, [])
        self.assertEqual((self.root / ".butler-name").read_text(encoding="utf-8"), "Bento\n")
        self.assertIn("installed (fresh), silently: no window opened", self.log())

    def test_without_butler_cmd_it_says_why_nothing_opened(self):
        (self.root / "bin" / "butler.cmd").unlink()
        self.install()
        self.assertEqual(self.opened, [])
        self.assertIn("butler.cmd is missing", self.log())

    def test_it_never_fails_the_install(self):
        with mock.patch.object(installed, "run", side_effect=OSError("disk full")):
            self.install()
        self.assertIn("the step after it failed (OSError: disk full)", self.log())


if __name__ == "__main__":
    unittest.main()
