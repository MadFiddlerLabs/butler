"""The installer package: the butler's name from the downloaded file's name, what postinstall does on
a first install and an upgrade, and (on a Mac with the packaging tools) the built package itself.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NAMES = REPO / "scripts" / "lib" / "names.sh"
POSTINSTALL = REPO / "packaging" / "macos" / "scripts" / "postinstall"


def bash(script: str, *args: str) -> str:
    return subprocess.run(["bash", "-c", f'. "{NAMES}"; {script}', "bash", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


class NameFromPackageTest(unittest.TestCase):
    CASES = {
        "Bento.pkg": "Bento",
        "/Users/sam/Downloads/Bento.pkg": "Bento",
        "Mr Hudson.pkg": "Mr Hudson",
        "Mr Hudson (1).pkg": "Mr Hudson",       # Chrome and Firefox, second download
        "Bento-2.pkg": "Bento",                 # Safari
        "Mr%20Hudson.pkg": "Mr Hudson",
        "José (3).pkg": "José",
        "R2-D2.pkg": "R2-D2",
        "O'Brien.pkg": "O'Brien",
        "Butler.pkg": "",                       # the generic download
        "Butler-unsigned.pkg": "",
        "Butler-latest.pkg": "",
        "Bad;Name.pkg": "",
        "<script>.pkg": "",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pkg": "",  # longer than 24
    }

    def test_cases(self):
        for filename, expected in self.CASES.items():
            self.assertEqual(bash('name_from_pkg "$1"', filename), expected, filename)

    def test_bash_and_python_agree_on_names(self):
        from butler.setup_settings import valid_name
        for name in ("Bento", "José", "Zoë-Ann", "O'Brien", "R2", "Bento!", "--", "a" * 25, "Mr Hudson"):
            in_bash = subprocess.run(["bash", "-c", f'. "{NAMES}"; valid_name "$1"', "bash", name]).returncode == 0
            self.assertEqual(in_bash, valid_name(name) is not None, name)


class PostinstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.scripts = self.tmp / "scripts"
        self.scripts.mkdir()
        shutil.copy(POSTINSTALL, self.scripts / "postinstall")
        shutil.copy(NAMES, self.scripts / "names.sh")
        self.home = self.tmp / "home"
        (self.home / "Butler" / "scripts").mkdir(parents=True)
        fakebin = self.tmp / "fakebin"
        fakebin.mkdir()
        self.opened = self.tmp / "opened.txt"
        (fakebin / "open").write_text(f'#!/bin/bash\necho "$*" >> "{self.opened}"\n')
        (fakebin / "open").chmod(0o755)
        self.env = {**os.environ, "HOME": str(self.home), "PATH": f"{fakebin}:/usr/bin:/bin"}
        self.env.pop("COMMAND_LINE_INSTALL", None)

    def run_postinstall(self, pkg_name: str, **env) -> None:
        proc = subprocess.run(["bash", str(self.scripts / "postinstall"), f"/Users/sam/Downloads/{pkg_name}",
                               str(self.home)], env={**self.env, **env}, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_first_install_names_the_butler_and_opens_the_first_run(self):
        self.run_postinstall("Bento (1).pkg")
        self.assertEqual((self.home / "Butler" / ".butler-name").read_text(), "Bento\n")
        self.assertIn("-a Terminal", self.opened.read_text())
        self.assertIn("first-run.command", self.opened.read_text())

    def test_generic_download_leaves_the_name_to_the_first_run(self):
        self.run_postinstall("Butler.pkg")
        self.assertFalse((self.home / "Butler" / ".butler-name").exists())
        self.assertTrue(self.opened.exists())

    def test_upgrade_changes_nothing_and_opens_nothing(self):
        (self.home / "Butler" / "config").mkdir()
        (self.home / "Butler" / "config" / "settings.yaml").write_text("name: Bento\n")
        (self.home / "Butler" / ".butler-name").write_text("Bento\n")
        self.run_postinstall("Jeeves.pkg")
        self.assertEqual((self.home / "Butler" / ".butler-name").read_text(), "Bento\n")
        self.assertFalse(self.opened.exists())

    def test_command_line_install_opens_nothing(self):
        self.run_postinstall("Bento.pkg", COMMAND_LINE_INSTALL="1")
        self.assertEqual((self.home / "Butler" / ".butler-name").read_text(), "Bento\n")
        self.assertFalse(self.opened.exists())


@unittest.skipUnless((REPO / ".git").exists(), "needs a git checkout")
class ArchiveTest(unittest.TestCase):
    """The package and scripts/install.sh copy the repository with git archive (or GitHub's archive
    tarball, which follows the same rules). The README's images and the screenshot font stay out."""

    def test_readme_images_and_fonts_stay_out_of_installs(self):
        files = subprocess.run("git archive --worktree-attributes HEAD | tar -t", shell=True, cwd=REPO,
                               capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertIn("README.md", files)
        self.assertIn("scripts/install.sh", files)
        self.assertEqual([f for f in files if f.startswith(("docs/images", "scripts/fonts"))], [])


@unittest.skipUnless(shutil.which("pkgbuild") and shutil.which("productbuild"), "needs macOS packaging tools")
class BuildPackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp())
        proc = subprocess.run([str(REPO / "packaging" / "macos" / "build-pkg.sh"), "--version", "v0.0.0-test",
                               "--out", str(cls.out)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cls.pkg = Path(proc.stdout.strip().splitlines()[-1])
        cls.expanded = cls.out / "expanded"
        subprocess.run(["pkgutil", "--expand", str(cls.pkg), str(cls.expanded)], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_unsigned_name(self):
        self.assertEqual(self.pkg.name, "Butler-unsigned.pkg")

    def test_installs_to_the_users_home(self):
        dist = (self.expanded / "Distribution").read_text()
        self.assertIn('enable_currentUserHome="true"', dist)
        self.assertIn('enable_localSystem="false"', dist)
        self.assertIn('<os-version min="14.0"/>', dist)

    def test_payload(self):
        files = subprocess.run(["pkgutil", "--payload-files", str(self.pkg)], capture_output=True,
                               text=True, check=True).stdout.splitlines()
        for path in ("./Butler/bin/butler", "./Butler/scripts/first-run.sh", "./Butler/scripts/first-run.command",
                     "./Butler/src/butler/cli.py", "./Butler/config/settings.example.yaml", "./Butler/.butler-version",
                     "./Butler/TERMS_VERSION"):
            self.assertIn(path, files)
        for path in files:
            self.assertFalse(path.startswith("./Butler/.github"), path)
            self.assertFalse(path.startswith("./Butler/packaging"), path)
            self.assertFalse(path.startswith(("./Butler/docs/images", "./Butler/scripts/fonts")), path)
            # Never the household's own files.
            self.assertNotRegex(path, r"^\./Butler/(config/(?!.*\.example\.)[^/]+\.yaml|data/|projects/|budget/budget\.xlsx)")

    def test_unsigned_build_names_its_version_for_the_updater(self):
        files = subprocess.run(["pkgutil", "--payload-files", str(self.pkg)], capture_output=True,
                               text=True, check=True).stdout.splitlines()
        self.assertIn("./Butler/.butler-unsigned-build", files)

    def test_signed_builds_never_carry_the_marker(self):
        script = (REPO / "packaging" / "macos" / "build-pkg.sh").read_text()
        self.assertIn('[ -n "$sign" ] || printf', script, "the marker is written only without --sign")

    def test_scripts(self):
        scripts = self.expanded / "Butler-component.pkg" / "Scripts"   # pkgutil --expand unpacks it
        self.assertTrue(os.access(scripts / "postinstall", os.X_OK))
        self.assertTrue((scripts / "names.sh").exists())


class ReleaseWorkflowTest(unittest.TestCase):
    """A signed release reaches students only after the published one updated itself to it, with
    every check in place (.github/workflows/release.yml, tests/e2e/signed-update.sh)."""

    def setUp(self):
        import yaml
        self.jobs = yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text())["jobs"]

    def test_publishing_waits_for_the_signed_update(self):
        publish = self.jobs["publish"]
        self.assertIn("signed-update", publish["needs"])
        self.assertIn("needs.signed-update.result == 'success'", publish["if"])
        self.assertIn("github.ref_type == 'tag'", publish["if"])
        steps = " ".join(s.get("run", "") for s in self.jobs["package"]["steps"])
        self.assertNotIn("gh release create", steps, "the package job never publishes")

    def test_signed_update_relaxes_nothing(self):
        job = self.jobs["signed-update"]
        steps = " ".join(s.get("run", "") for s in job["steps"])
        self.assertIn("tests/e2e/signed-update.sh", steps)
        test = (REPO / "tests" / "e2e" / "signed-update.sh").read_text()
        for text in (str(job), test):
            self.assertNotRegex(text, r"BUTLER_UPDATE_ALLOW_UNSIGNED=|BUTLER_RELEASES_URL")


if __name__ == "__main__":
    unittest.main()
