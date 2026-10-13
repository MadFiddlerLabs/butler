"""The installer package: the butler's name from the downloaded file's name, what postinstall does on
a first install and an upgrade, and (on a Mac with the packaging tools) the built package itself.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import os
import re
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
                     "./Butler/TERMS_VERSION", "./Butler/requirements.lock", "./Butler/scripts/lib/uv.sh",
                     "./Butler/scripts/bootstrap.sh"):
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


def read_lock() -> dict[str, dict]:
    """requirements.lock as {name: {"version", "hashes", "via"}}: uv's format, one entry per
    package, its hashes on continuation lines and a '# via' block naming who needs it."""
    entries, current, in_via = {}, None, False
    for line in (REPO / "requirements.lock").read_text().splitlines():
        if m := re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^ ;\\]+)", line):
            current = entries[canonical(m[1])] = {"version": m[2], "hashes": [], "via": [], "line": line}
            in_via = False
        elif current and (m := re.match(r"^\s+--hash=sha256:([0-9a-f]{64})", line)):
            current["hashes"].append(m[1])
        elif current and (m := re.match(r"^\s+# via ?(.*)$", line)):
            in_via = True
            if m[1]:
                current["via"].append(m[1].strip())
        elif current and in_via and (m := re.match(r"^\s+#\s+(.+)$", line)):
            current["via"].append(m[1].strip())
    return entries


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


class LockTest(unittest.TestCase):
    """Every install of a version gets the packages its tests ran with: requirements.lock pins
    each one, with hashes, and can't drift from requirements.txt (docs/UPDATES.md, "Dependencies")."""

    def setUp(self):
        from packaging.requirements import Requirement
        self.lock = read_lock()
        self.wanted = [Requirement(line) for line in (REPO / "requirements.txt").read_text().splitlines()
                       if line.strip() and not line.startswith("#")]

    def test_every_requirement_is_locked_to_a_version_it_allows(self):
        for req in self.wanted:
            entry = self.lock.get(canonical(req.name))
            self.assertIsNotNone(entry, f"{req.name} is in requirements.txt but not in the lock: run scripts/lock.sh")
            self.assertTrue(req.specifier.contains(entry["version"], prereleases=True),
                            f"the lock pins {req.name}=={entry['version']}, outside {req.specifier}: run scripts/lock.sh")
            self.assertIn("-r requirements.txt", entry["via"], req.name)

    def test_nothing_is_locked_that_requirements_txt_dropped(self):
        named = {canonical(r.name) for r in self.wanted}
        for name, entry in self.lock.items():
            if "-r requirements.txt" in entry["via"]:
                self.assertIn(name, named, f"{name} left requirements.txt but not the lock: run scripts/lock.sh")
            else:
                self.assertTrue(entry["via"], f"{name} is in the lock, but nothing needs it")

    def test_every_package_is_pinned_with_hashes(self):
        self.assertGreater(len(self.lock), len(self.wanted), "the lock holds the dependencies too")
        for name, entry in self.lock.items():
            self.assertTrue(entry["hashes"], f"{name} has no hash")

    def test_the_lock_covers_the_pythons_that_use_it(self):
        lock_sh = (REPO / "scripts" / "lock.sh").read_text()
        self.assertIn("--universal", lock_sh, "one lock for Apple silicon, Intel and CI's Linux")
        self.assertIn("--generate-hashes", lock_sh)
        lowest = re.search(r"--python-version (3\.\d+)", lock_sh)[1]
        bootstrap = re.search(r"--python (3\.\d+)", (REPO / "scripts" / "bootstrap.sh").read_text())[1]
        ci = re.findall(r'python-version: "(3\.\d+)"', (REPO / ".github" / "workflows" / "tests.yml").read_text())
        for version in [bootstrap, *ci]:
            self.assertGreaterEqual(tuple(map(int, version.split("."))), tuple(map(int, lowest.split("."))), version)

    def test_bootstrap_installs_exactly_the_lock_with_hashes_and_no_builds(self):
        script = (REPO / "scripts" / "bootstrap.sh").read_text()
        self.assertRegex(script, r"pip sync [^\n]*--require-hashes[^\n]*--no-build[^\n]*requirements\.lock")
        self.assertNotIn("requirements.txt", script.split("set -euo pipefail")[1])
        self.assertNotRegex(script, r"curl[^\n]*\|\s*(env [^|]*)?sh", "no installer piped from the network")
        self.assertIn('. "$root/scripts/lib/uv.sh"', script)

    def test_uv_is_pinned_by_version_and_checksum(self):
        uv_sh = (REPO / "scripts" / "lib" / "uv.sh").read_text()
        self.assertRegex(uv_sh, r'BUTLER_UV_VERSION="\d+\.\d+\.\d+"')
        for target in ("aarch64-apple-darwin", "x86_64-apple-darwin"):
            self.assertRegex(uv_sh, rf'{target}\) echo "[0-9a-f]{{64}}"', target)
        self.assertIn('[ "$got" != "$sha" ]', uv_sh, "a download that doesn't match is never used")

    def test_ci_tests_the_locked_packages_and_checks_the_lock(self):
        workflows = REPO / ".github" / "workflows"
        self.assertIn("scripts/lock.sh --check", (workflows / "tests.yml").read_text())
        for path in workflows.glob("*.yml"):
            text = path.read_text()
            if re.search(r"pip install [^\n]*-r requirements", text):
                self.assertIn("pip install --require-hashes -r requirements.lock", text, path.name)


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

    def test_signed_update_runs_from_every_origin(self):
        origins, job = self.jobs["origins"], self.jobs["signed-update"]
        self.assertIn("origins", job["needs"])
        self.assertEqual(job["strategy"]["matrix"]["old"], "${{ fromJSON(needs.origins.outputs.versions) }}")
        self.assertFalse(job["strategy"]["fail-fast"], "each origin reports on its own")
        self.assertEqual(job["env"]["OLD"], "${{ matrix.old }}")
        self.assertIn('"$NEW" "$OLD"', " ".join(s.get("run", "") for s in job["steps"]))
        pick = " ".join(s.get("run", "") for s in origins["steps"])
        self.assertIn(".github/scripts/update_origins.py", pick)
        self.assertIn("https://buildyourbutler.com/updates/stable.json", pick)
        self.assertIn("isPrerelease", pick)
        self.assertEqual(origins["if"], job["if"])

    def test_signed_update_relaxes_nothing(self):
        job = self.jobs["signed-update"]
        steps = " ".join(s.get("run", "") for s in job["steps"])
        self.assertIn("tests/e2e/signed-update.sh", steps)
        test = (REPO / "tests" / "e2e" / "signed-update.sh").read_text()
        for text in (str(job), test):
            self.assertNotRegex(text, r"BUTLER_UPDATE_ALLOW_UNSIGNED=|BUTLER_RELEASES_URL")


class UpdateOriginsTest(unittest.TestCase):
    """Which published releases a candidate must update from (.github/scripts/update_origins.py)."""

    RELEASES = [{"tagName": t, "isDraft": False, "isPrerelease": False} for t in ("v0.3.1", "v0.3.0", "v0.2.0", "v0.1.0")]

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("update_origins", REPO / ".github" / "scripts" / "update_origins.py")
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def origins(self, candidate="v0.4.0", releases=None, min_version=None):
        return self.mod.origins(candidate, self.RELEASES if releases is None else releases,
                                {"available": True, "version": "v0.3.1", "min_version": min_version})

    def test_the_latest_and_the_first_signed_release(self):
        self.assertEqual(self.origins(), ["v0.3.1", "v0.1.0"])

    def test_min_version_moves_the_oldest_up(self):
        self.assertEqual(self.origins(min_version="v0.2.0"), ["v0.3.1", "v0.2.0"])
        self.assertEqual(self.origins(min_version="v0.2.1"), ["v0.3.1", "v0.3.0"])
        self.assertEqual(self.origins(min_version="v0.3.1"), ["v0.3.1"], "the same release once")
        self.assertEqual(self.origins(min_version="v0.9.0"), ["v0.3.1"], "the latest is always tested")

    def test_only_published_signed_releases_older_than_the_candidate(self):
        releases = self.RELEASES + [{"tagName": "v0.0.0-abc1234", "isDraft": False, "isPrerelease": True},
                                    {"tagName": "v0.0.9", "isDraft": False, "isPrerelease": True},
                                    {"tagName": "v0.3.9", "isDraft": True, "isPrerelease": False},
                                    {"tagName": "v1.0.0", "isDraft": False, "isPrerelease": False},
                                    {"tagName": "nightly", "isDraft": False, "isPrerelease": False}]
        self.assertEqual(self.origins("v0.3.2", releases), ["v0.3.1", "v0.1.0"])
        self.assertEqual(self.origins("v0.1.1"), ["v0.1.0"])

    def test_nothing_to_update_from_is_an_error(self):
        for candidate, releases in (("v0.3.1", None), ("v0.1.0", None), ("v0.4.0", []), ("main", None)):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                self.origins(candidate, releases)

    def test_the_command_prints_json_or_says_why(self):
        import io
        import json
        from contextlib import redirect_stderr, redirect_stdout
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "releases.json").write_text(json.dumps(self.RELEASES))
        (tmp / "manifest.json").write_text(json.dumps({"min_version": None}))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(self.mod.main(["v0.4.0", str(tmp / "releases.json"), str(tmp / "manifest.json")]), 0)
            self.assertEqual(self.mod.main(["v0.3.1", str(tmp / "releases.json"), str(tmp / "manifest.json")]), 1)
        self.assertEqual(json.loads(out.getvalue()), ["v0.3.1", "v0.1.0"])
        self.assertIn("v0.3.1 is already released", err.getvalue())


if __name__ == "__main__":
    unittest.main()
