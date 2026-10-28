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


INSTALL_LIB = REPO / "scripts" / "lib" / "install.sh"


class InstallStateTest(unittest.TestCase):
    """scripts/lib/install.sh: what the installer finds, and which Terminal script it opens."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def state(self) -> tuple[str, str]:
        out = subprocess.run(["bash", "-c", f'. "{INSTALL_LIB}"; s="$(install_state "$1")"; echo "$s $(install_opens "$s")"',
                              "bash", str(self.dir)], capture_output=True, text=True, check=True).stdout.split()
        return out[0], out[1]

    def touch(self, rel: str) -> None:
        (self.dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.dir / rel).write_text("x\n")

    def test_nothing_yet_is_a_fresh_install(self):
        self.assertEqual(self.state(), ("fresh", "first-run.command"))
        self.touch(".butler-name")        # the installer's own work, not the household's
        self.touch("logs/install.log")
        self.assertEqual(self.state(), ("fresh", "first-run.command"))

    def test_a_first_run_that_began_is_started(self):
        self.touch("data/first-run-started")
        self.assertEqual(self.state(), ("started", "welcome-back.command"))

    def test_settings_alone_are_started(self):
        """The first run writes settings early (`butler name`): that is not the end of it. This
        is the case the old installer took for an upgrade, and opened nothing."""
        self.touch("config/settings.yaml")
        self.assertEqual(self.state(), ("started", "welcome-back.command"))

    def test_the_marker_means_finished(self):
        self.touch("data/first-run-started")
        self.touch("data/first-run-done")
        self.assertEqual(self.state(), ("finished", "welcome-back.command"))

    def test_a_butler_from_before_the_marker_with_a_brief_is_finished(self):
        self.touch("config/settings.yaml")
        self.touch("out/preview/brief-2026-10-12.html")
        self.assertEqual(self.state(), ("finished", "welcome-back.command"))


class AppPlaceTest(unittest.TestCase):
    """app_place in scripts/lib/install.sh: where the Butler app goes. system is /Applications,
    home is ~/Applications."""

    def place(self, admin: str, writable: str, there: str, upgrade: str) -> str:
        return subprocess.run(["bash", "-c", f'. "{INSTALL_LIB}"; app_place "$@"', "bash", admin, writable, there,
                               upgrade], capture_output=True, text=True, check=True).stdout.strip()

    def test_every_case(self):
        cases = {
            # admin, writable, /Applications/Butler.app, upgrade: place
            ("yes", "yes", "none", "no"): "system",     # an admin's first install: where Finder shows it
            ("no", "yes", "none", "no"): "home",        # not an admin: never asks for a password
            ("no", "no", "none", "no"): "home",
            ("yes", "no", "none", "no"): "home",        # an admin on a Mac whose /Applications is locked
            ("yes", "yes", "none", "yes"): "home",      # an upgrade keeps the app in ~/Applications
            ("yes", "yes", "ours", "yes"): "system",    # ... or in /Applications, kept current there
            ("yes", "yes", "ours", "no"): "system",
            ("no", "yes", "ours", "yes"): "system",
            ("yes", "no", "ours", "yes"): "home",       # can't replace it: the new one stays at home
        }
        for admin in ("yes", "no"):
            for writable in ("yes", "no"):
                for upgrade in ("yes", "no"):
                    cases[(admin, writable, "other", upgrade)] = "home"   # never another app's place
        for args, expected in cases.items():
            self.assertEqual(self.place(*args), expected, args)


class AppBundleIdTest(unittest.TestCase):
    """app_bundle_id reads the identifier with plutil, or by hand where there's no plutil."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def bundle_id(self, app: Path, path: str | None = None) -> str:
        env = {**os.environ, **({"PATH": path} if path else {})}
        return subprocess.run(["/bin/bash", "-c", f'. "{INSTALL_LIB}"; app_bundle_id "$1"', "bash", str(app)],
                              env=env, capture_output=True, text=True, check=True).stdout.strip()

    def test_with_and_without_plutil(self):
        tools = self.tmp / "tools"          # tr and sed, and no plutil
        tools.mkdir()
        for tool in ("tr", "sed"):
            (tools / tool).symlink_to(shutil.which(tool))
        app = make_app(self.tmp / "Butler.app")
        for path in (None, str(tools)):
            self.assertEqual(self.bundle_id(app, path), "com.buildyourbutler.butler-app", path)
            self.assertEqual(self.bundle_id(self.tmp / "Missing.app", path), "", path)
        (app / "Contents" / "Info.plist").write_bytes(b"bplist00\x00\x01binary")
        self.assertEqual(self.bundle_id(app, str(tools)), "", "a plist it can't read is not ours")


def make_app(path: Path, bundle_id: str = "com.buildyourbutler.butler-app", version: str = "new") -> Path:
    """A stand-in Butler.app: an XML Info.plist with its identifier, and a binary that names its version."""
    (path / "Contents" / "MacOS").mkdir(parents=True)
    (path / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0">\n<dict>\n'
        f'    <key>CFBundleIdentifier</key>\n    <string>{bundle_id}</string>\n</dict>\n</plist>\n')
    (path / "Contents" / "MacOS" / "Butler").write_text(f"#!/bin/sh\n# {version}\n")
    return path


class PostinstallTest(unittest.TestCase):
    """packaging/macos/scripts/postinstall, run as Installer runs it for the user (not root), with
    `launchctl` and `open` replaced by recorders. COMMAND_LINE_INSTALL is unset, as in Installer.app."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.scripts = self.tmp / "scripts"
        self.scripts.mkdir()
        shutil.copy(POSTINSTALL, self.scripts / "postinstall")
        shutil.copy(NAMES, self.scripts / "names.sh")
        shutil.copy(INSTALL_LIB, self.scripts / "install.sh")
        self.home = self.tmp / "home"
        self.butler = self.home / "Butler"
        (self.butler / "scripts").mkdir(parents=True)
        (self.butler / ".butler-version").write_text("v0.5.0\n")
        fakebin = self.tmp / "fakebin"
        fakebin.mkdir()
        self.opened = self.tmp / "opened.txt"
        self.launchctl = self.tmp / "launchctl.txt"
        (fakebin / "open").write_text(f'#!/bin/bash\necho "$*" >> "{self.opened}"\nexit "${{FAKE_OPEN_EXIT:-0}}"\n')
        # `launchctl asuser <uid> <command…>` runs the command in that user's session.
        (fakebin / "launchctl").write_text(
            f'#!/bin/bash\necho "$*" >> "{self.launchctl}"\n'
            '[ -n "${FAKE_ASUSER_FAILS:-}" ] && exit 5\n'
            '[ "$1" = asuser ] && shift 2 && exec "$@"\nexit 0\n')
        # The Mac's own tools, recorded: membership of the admin group, ditto, and the signature check.
        (fakebin / "dseditgroup").write_text(
            f'#!/bin/bash\necho "$*" >> "{self.tmp}/dseditgroup.txt"\n[ -n "${{FAKE_ADMIN:-}}" ]\n')
        (fakebin / "ditto").write_text('#!/bin/bash\n[ -n "${FAKE_DITTO_FAILS:-}" ] && exit 1\ncp -R "$1" "$2"\n')
        (fakebin / "codesign").write_text('#!/bin/bash\n[ -z "${FAKE_CODESIGN_FAILS:-}" ]\n')
        # Who is at the screen: this user, unless FAKE_CONSOLE_USER says someone else is.
        (fakebin / "stat").write_text(
            '#!/bin/bash\nif [ "$*" = "-f %Su /dev/console" ]; then echo "${FAKE_CONSOLE_USER:-$(id -un)}"; exit 0; fi\n'
            'exec /usr/bin/stat "$@"\n')
        for f in ("open", "launchctl", "dseditgroup", "ditto", "codesign", "stat"):
            (fakebin / f).chmod(0o755)
        self.apps = self.tmp / "Applications"          # stands in for /Applications
        self.apps.mkdir()
        self.env = {**os.environ, "HOME": str(self.home), "PATH": f"{fakebin}:/usr/bin:/bin",
                    "BUTLER_SYSTEM_APPS": str(self.apps)}
        for inherited in ("COMMAND_LINE_INSTALL", "BUTLER_PKG_NO_OPEN"):
            self.env.pop(inherited, None)

    def run_postinstall(self, pkg_name: str, **env) -> None:
        proc = subprocess.run(["bash", str(self.scripts / "postinstall"), f"/Users/sam/Downloads/{pkg_name}",
                               str(self.home)], env={**self.env, **env}, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def log(self) -> str:
        return (self.butler / "logs" / "install.log").read_text()

    def test_first_install_names_the_butler_and_opens_the_first_run_in_the_users_session(self):
        self.run_postinstall("Bento (1).pkg")
        self.assertEqual((self.butler / ".butler-name").read_text(), "Bento\n")
        self.assertEqual(self.opened.read_text().strip(), f"-a Terminal {self.butler}/scripts/first-run.command")
        self.assertEqual(self.launchctl.read_text().split()[:3], ["asuser", str(os.getuid()), "open"])
        self.assertRegex(self.log(), r"v0\.5\.0 installed \(fresh\): opened Terminal with first-run\.command "
                                     r"\(launchctl asuser \d+ open\)")

    def test_butler_works_in_terminal_even_if_no_window_opens(self):
        """The Done screen's fallback is "open Terminal and type butler": the PATH line, once."""
        self.run_postinstall("Bento.pkg", FAKE_ASUSER_FAILS="1", FAKE_OPEN_EXIT="1")
        self.run_postinstall("Bento.pkg")
        profile = (self.home / ".zprofile").read_text()
        self.assertEqual(profile.count(f'export PATH="{self.butler}/bin:$PATH"'), 1)

    def test_generic_download_leaves_the_name_to_the_first_run(self):
        self.run_postinstall("Butler.pkg")
        self.assertFalse((self.butler / ".butler-name").exists())
        self.assertIn("first-run.command", self.opened.read_text())

    def test_a_half_done_first_run_opens_the_way_back(self):
        """The bug this replaces: settings.yaml (written early by the first run) was taken for an
        upgrade, so a reinstall opened nothing while the Done screen said Terminal was opening."""
        (self.butler / "config").mkdir()
        (self.butler / "config" / "settings.yaml").write_text("name: Bento\n")
        (self.butler / ".butler-name").write_text("Bento\n")
        self.run_postinstall("Jeeves.pkg")
        self.assertEqual((self.butler / ".butler-name").read_text(), "Bento\n", "the name stays")
        self.assertEqual((self.butler / "config" / "settings.yaml").read_text(), "name: Bento\n")
        self.assertIn("welcome-back.command", self.opened.read_text())
        self.assertIn("installed (started): opened Terminal with welcome-back.command", self.log())

    def test_over_a_finished_butler_it_offers_the_choice_too(self):
        (self.butler / "data").mkdir()
        (self.butler / "data" / "first-run-done").write_text("2026-10-12T08:00:00Z\n")
        self.run_postinstall("Bento.pkg")
        self.assertIn("welcome-back.command", self.opened.read_text())
        self.assertIn("installed (finished)", self.log())

    def test_without_launchctl_asuser_it_still_opens_terminal_and_says_how(self):
        self.run_postinstall("Bento.pkg", FAKE_ASUSER_FAILS="1")
        self.assertIn("first-run.command", self.opened.read_text())
        self.assertIn("opened Terminal with first-run.command (open)", self.log())

    def test_a_student_who_isnt_at_the_screen_gets_no_window_in_someone_elses_session(self):
        """Found by tests/e2e/standard-user.sh: run for an account with no screen session, the plain
        `open` fallback opened Terminal in the session of whoever was at the screen."""
        self.run_postinstall("Bento.pkg", FAKE_ASUSER_FAILS="1", FAKE_CONSOLE_USER="someone-else")
        self.assertFalse(self.opened.exists(), "Terminal opened in another account's session")
        self.assertRegex(self.log(), r"installed \(fresh\): could not open Terminal with first-run\.command "
                                     r"\(launchctl asuser \d+ open; \S+ isn't the one at the screen, so not open, exit 5\)")

    def test_run_as_root_it_finds_a_home_folder_with_a_space(self):
        """Found by tests/e2e/standard-user.sh: run as root, postinstall read the console user's home
        from dscl with awk's second word, which a path with a space ("/Users/byb test") breaks:
        dscl puts such a path on its own line. The PATH line and the Butler app then went nowhere."""
        home = self.tmp / "byb test"
        butler = home / "Butler"
        home.mkdir()
        (self.home / "Butler").rename(butler)
        self.home = home                     # Installer's target: the student's home
        app = home / "Applications" / "Butler.app" / "Contents"
        app.mkdir(parents=True)
        (app / "Info.plist").write_text("<plist><dict><key>CFBundleIdentifier</key>"
                                        "<string>com.buildyourbutler.butler-app</string></dict></plist>")
        fakebin = self.tmp / "rootbin"
        fakebin.mkdir()
        me = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
        scripts = {
            # Root, as Installer may run it; the student is at the screen.
            "id": '#!/bin/bash\n[ "$*" = "-u" ] && { echo 0; exit 0; }\n[ "$*" = "-un" ] && { echo root; exit 0; }\n'
                  'exec /usr/bin/id "$@"\n',
            "dscl": f'#!/bin/bash\nprintf "NFSHomeDirectory:\\n {home}\\n"\n',
            "chown": "#!/bin/bash\nexit 0\n",
            "sudo": '#!/bin/bash\n[ "$1" = -u ] && shift 2\nexec "$@"\n',
        }
        for name, text in scripts.items():
            (fakebin / name).write_text(text)
            (fakebin / name).chmod(0o755)
        self.run_postinstall("Bento.pkg", HOME="/var/root", FAKE_CONSOLE_USER=me,
                             PATH=f"{fakebin}:{self.env['PATH']}")
        self.assertIn(f'export PATH="{butler}/bin:$PATH"', (home / ".zprofile").read_text())
        self.assertEqual((butler / ".butler-name").read_text(), "Bento\n")
        self.assertIn(f"-a Terminal {butler}/scripts/first-run.command", self.opened.read_text())
        log = (butler / "logs" / "install.log").read_text()
        self.assertIn("the Butler app stays in ~/Applications", log)
        self.assertNotIn("no new Butler app", log)

    def test_when_terminal_wont_open_the_log_says_so_and_the_install_still_succeeds(self):
        self.run_postinstall("Bento.pkg", FAKE_ASUSER_FAILS="1", FAKE_OPEN_EXIT="1")
        self.assertIn("could not open Terminal with first-run.command (open, exit 1)", self.log())

    def test_command_line_install_opens_nothing(self):
        """The overnight updater and CI install with `installer`, which sets COMMAND_LINE_INSTALL."""
        self.run_postinstall("Bento.pkg", COMMAND_LINE_INSTALL="1")
        self.assertEqual((self.butler / ".butler-name").read_text(), "Bento\n")
        self.assertFalse(self.opened.exists())
        self.assertFalse(self.launchctl.exists())
        self.assertIn("installed (fresh), from the command line: Terminal not opened", self.log())

    # ---------- the Butler app: /Applications when it may, ~/Applications otherwise ----------

    def lay_app(self, version: str = "new") -> Path:
        """What the package's payload does: the app in ~/Applications."""
        return make_app(self.home / "Applications" / "Butler.app", version=version)

    def upgrade(self) -> None:
        """A household whose first run began: a later install is an upgrade."""
        (self.butler / "data").mkdir(exist_ok=True)
        (self.butler / "data" / "first-run-started").write_text("2026-10-12T08:00:00Z\n")

    def binary(self, app: Path) -> str:
        return (app / "Contents" / "MacOS" / "Butler").read_text()

    def test_an_admin_gets_the_app_in_applications(self):
        self.lay_app()
        self.run_postinstall("Bento.pkg", FAKE_ADMIN="1")
        self.assertIn("# new", self.binary(self.apps / "Butler.app"))
        self.assertFalse((self.home / "Applications").exists(), "no copy left, and no empty folder")
        self.assertIn(f"the Butler app is in {self.apps} (moved, from ~/Applications)", self.log())
        self.assertEqual((self.tmp / "dseditgroup.txt").read_text().split(),
                         ["-o", "checkmember", "-m", subprocess.run(["id", "-un"], capture_output=True,
                                                                    text=True).stdout.strip(), "admin"])
        self.assertEqual([p.name for p in self.apps.iterdir()], ["Butler.app"], "no staging copy left behind")

    def test_the_command_line_first_install_does_the_same(self):
        """CI and the hosted installer's package path: `installer`, on a household with nothing yet."""
        self.lay_app()
        self.run_postinstall("Bento.pkg", FAKE_ADMIN="1", COMMAND_LINE_INSTALL="1")
        self.assertTrue((self.apps / "Butler.app").exists())
        self.assertFalse((self.home / "Applications" / "Butler.app").exists())

    def test_not_an_admin_keeps_the_app_in_the_home_folder_without_asking(self):
        self.lay_app()
        self.run_postinstall("Bento.pkg")
        self.assertTrue((self.home / "Applications" / "Butler.app").exists())
        self.assertEqual(list(self.apps.iterdir()), [])
        self.assertIn("the Butler app stays in ~/Applications:", self.log())
        self.assertIn("isn't an admin", self.log())
        self.assertIn("opened Terminal with first-run.command", self.log(), "and the install goes on")

    @unittest.skipIf(os.geteuid() == 0, "root can write anywhere")
    def test_a_locked_applications_folder_keeps_the_app_at_home(self):
        self.lay_app()
        self.apps.chmod(0o555)
        self.addCleanup(self.apps.chmod, 0o755)
        self.run_postinstall("Bento.pkg", FAKE_ADMIN="1")
        self.assertTrue((self.home / "Applications" / "Butler.app").exists())
        self.assertIn(f"{self.apps} isn't writable for", self.log())

    def test_an_upgrade_replaces_our_app_in_applications(self):
        make_app(self.apps / "Butler.app", version="old")
        self.upgrade()
        self.lay_app("new")
        self.run_postinstall("Bento.pkg", FAKE_ADMIN="1", COMMAND_LINE_INSTALL="1")
        self.assertIn("# new", self.binary(self.apps / "Butler.app"))
        self.assertFalse((self.home / "Applications" / "Butler.app").exists())
        self.assertIn("(replaced the older one, from ~/Applications)", self.log())
        self.assertEqual([p.name for p in self.apps.iterdir()], ["Butler.app"])

    def test_an_upgrade_keeps_an_app_that_was_at_home_at_home(self):
        """The overnight updater never moves the app a student may have put in the Dock."""
        self.upgrade()
        self.lay_app()
        self.run_postinstall("Bento.pkg", FAKE_ADMIN="1", COMMAND_LINE_INSTALL="1")
        self.assertTrue((self.home / "Applications" / "Butler.app").exists())
        self.assertEqual(list(self.apps.iterdir()), [])
        self.assertIn("the Butler app stays in ~/Applications, where it was", self.log())

    def test_installing_again_from_the_website_moves_an_admins_app_to_applications(self):
        """Installer.app over a v0.5.0 Butler (the app at home): the student chose to install, so
        the app goes where Finder shows it."""
        self.upgrade()
        self.lay_app()
        self.run_postinstall("Bento.pkg", FAKE_ADMIN="1")
        self.assertTrue((self.apps / "Butler.app").exists())
        self.assertFalse((self.home / "Applications" / "Butler.app").exists())

    def test_another_app_called_butler_is_never_touched(self):
        for upgrade in (False, True):
            with self.subTest(upgrade=upgrade):
                other = self.apps / "Butler.app"
                shutil.rmtree(other, ignore_errors=True)
                make_app(other, bundle_id="com.example.someone-elses-butler", version="theirs")
                if upgrade:
                    self.upgrade()
                if not (self.home / "Applications" / "Butler.app").exists():
                    self.lay_app()
                self.run_postinstall("Bento.pkg", FAKE_ADMIN="1", **({"COMMAND_LINE_INSTALL": "1"} if upgrade else {}))
                self.assertIn("# theirs", self.binary(other))
                self.assertEqual(sorted(p.name for p in self.apps.iterdir()), ["Butler.app"])
                self.assertTrue((self.home / "Applications" / "Butler.app").exists())
                self.assertIn("is another app, left alone", self.log())

    def test_a_link_called_butler_is_not_ours_either(self):
        elsewhere = make_app(self.tmp / "elsewhere" / "Butler.app", version="theirs")
        (self.apps / "Butler.app").symlink_to(elsewhere)
        self.lay_app()
        self.run_postinstall("Bento.pkg", FAKE_ADMIN="1")
        self.assertTrue((self.apps / "Butler.app").is_symlink())
        self.assertIn("# theirs", self.binary(elsewhere))
        self.assertIn("is another app, left alone", self.log())

    def test_a_copy_that_fails_leaves_everything_as_it_was(self):
        for failure in ("FAKE_DITTO_FAILS", "FAKE_CODESIGN_FAILS"):
            with self.subTest(failure=failure):
                shutil.rmtree(self.apps / "Butler.app", ignore_errors=True)
                make_app(self.apps / "Butler.app", version="old")
                self.upgrade()
                if not (self.home / "Applications" / "Butler.app").exists():
                    self.lay_app()
                self.run_postinstall("Bento.pkg", FAKE_ADMIN="1", COMMAND_LINE_INSTALL="1", **{failure: "1"})
                self.assertIn("# old", self.binary(self.apps / "Butler.app"), "the working app stays")
                self.assertEqual(sorted(p.name for p in self.apps.iterdir()), ["Butler.app"], "no half copy")
                self.assertTrue((self.home / "Applications" / "Butler.app").exists())
                self.assertIn(f"copying it to {self.apps} failed", self.log())

    def test_other_apps_in_the_home_applications_folder_stay(self):
        self.lay_app()
        make_app(self.home / "Applications" / "Other.app", bundle_id="com.example.other")
        self.run_postinstall("Bento.pkg", FAKE_ADMIN="1")
        self.assertEqual([p.name for p in (self.home / "Applications").iterdir()], ["Other.app"])

    def test_app_location_finds_ours_first_in_applications(self):
        def where() -> str:
            return subprocess.run(["bash", "-c", f'. "{INSTALL_LIB}"; app_location "$1"', "bash", str(self.home)],
                                  env=self.env, capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(where(), "")
        self.lay_app()
        self.assertEqual(where(), str(self.home / "Applications" / "Butler.app"))
        make_app(self.apps / "Butler.app", bundle_id="com.example.someone-elses-butler")
        self.assertEqual(where(), str(self.home / "Applications" / "Butler.app"), "not someone else's")
        shutil.rmtree(self.apps / "Butler.app")
        make_app(self.apps / "Butler.app")
        self.assertEqual(where(), str(self.apps / "Butler.app"))

    def test_the_scripts_it_opens_exist_and_are_executable(self):
        for name in ("first-run.command", "welcome-back.command", "butler.command"):
            path = REPO / "scripts" / name
            self.assertTrue(os.access(path, os.X_OK), name)
        self.assertIn("first-run.sh\" --again", (REPO / "scripts" / "welcome-back.command").read_text())
        self.assertIn("bin/butler", (REPO / "scripts" / "butler.command").read_text())


class LaunchdInstallTest(unittest.TestCase):
    """launchd/install.sh, the schedule's install (/butler-setup inbox), with `launchctl` recorded and
    a stand-in for Butler's Python that answers the send hours."""

    def test_a_new_account_without_launch_agents_and_a_space_in_its_home(self):
        """Found by tests/e2e/standard-user.sh: a Mac account that has never had a launch agent has
        no ~/Library/LaunchAgents, and install.sh wrote into it without making it."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        home = tmp / "byb test"
        root = home / "Butler"
        shutil.copytree(REPO / "launchd", root / "launchd")
        (root / ".venv" / "bin").mkdir(parents=True)
        (root / ".venv" / "bin" / "python").write_text("#!/bin/bash\necho 7 17\n")
        fakebin = tmp / "fakebin"
        fakebin.mkdir()
        (fakebin / "launchctl").write_text(f'#!/bin/bash\necho "$*" >> "{tmp}/launchctl.txt"\n')
        for f in (root / ".venv" / "bin" / "python", fakebin / "launchctl"):
            f.chmod(0o755)
        proc = subprocess.run(["bash", str(root / "launchd" / "install.sh")], capture_output=True, text=True,
                              env={**os.environ, "HOME": str(home), "PATH": f"{fakebin}:/usr/bin:/bin"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        agents = home / "Library" / "LaunchAgents"
        for label in ("butler", "morning", "portal"):
            plist = (agents / f"com.buildyourbutler.{label}.plist").read_text()
            self.assertIn(f"<string>{root}/run.sh</string>", plist)
            self.assertNotIn("__", plist)
            self.assertIn(f"bootstrap gui/{os.getuid()} {agents}/com.buildyourbutler.{label}.plist",
                          (tmp / "launchctl.txt").read_text())


class DoneScreenTest(unittest.TestCase):
    """The installer's last screen, for someone who has never used Terminal."""

    def test_plain_words_and_a_one_line_fallback(self):
        text = (REPO / "packaging" / "macos" / "resources" / "conclusion.html").read_text()
        words = re.sub(r"<[^>]+>", "", text)
        self.assertNotIn("first-run.command", words, "double-clicking a script isn't the normal path")
        self.assertNotIn("double-click", words.lower())
        self.assertIn("type butler", " ".join(words.split()))
        self.assertIn("Butler app", words)


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
        # The Butler app needs the Swift compiler (every Mac with Xcode's tools, and CI's runners).
        cls.app = None
        if shutil.which("swiftc"):
            cls.app = cls.out / "Butler.app"
            subprocess.run([str(REPO / "packaging" / "macos" / "build-app.sh"), str(cls.app), "v0.0.0-test"],
                           check=True, capture_output=True)
        proc = subprocess.run([str(REPO / "packaging" / "macos" / "build-pkg.sh"), "--version", "v0.0.0-test",
                               "--out", str(cls.out), *(["--app", str(cls.app)] if cls.app else [])],
                              capture_output=True, text=True)
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
        self.assertTrue((scripts / "install.sh").exists())

    def test_the_scripts_the_installer_opens_are_in_the_package(self):
        files = subprocess.run(["pkgutil", "--payload-files", str(self.pkg)], capture_output=True,
                               text=True, check=True).stdout.splitlines()
        for path in ("./Butler/scripts/welcome-back.command", "./Butler/scripts/butler.command",
                     "./Butler/scripts/lib/install.sh", "./Butler/.claude/settings.json"):
            self.assertIn(path, files)

    def test_the_butler_app_goes_to_applications_and_stays_there(self):
        if not self.app:
            self.skipTest("needs swiftc")
        files = subprocess.run(["pkgutil", "--payload-files", str(self.pkg)], capture_output=True,
                               text=True, check=True).stdout.splitlines()
        self.assertIn("./Applications/Butler.app/Contents/MacOS/Butler", files)
        self.assertIn("./Applications/Butler.app/Contents/Info.plist", files)
        info = (self.expanded / "Butler-component.pkg" / "PackageInfo").read_text()
        # Never moved into another app with the same identifier somewhere else on the Mac.
        self.assertIn('relocatable="false"', info)
        self.assertNotRegex(info, r"<relocate>\s*<bundle")
        self.assertEqual(subprocess.run(["codesign", "--verify", "--strict", str(self.app)]).returncode, 0)
        archs = subprocess.run(["lipo", "-archs", str(self.app / "Contents" / "MacOS" / "Butler")],
                               capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(sorted(archs), ["arm64", "x86_64"])


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

    def test_windows_bootstrap_reads_the_same_pins_and_checks_them(self):
        """bootstrap.ps1 (#40) has no pins of its own: it reads uv.sh's, and refuses a download
        whose SHA-256 doesn't match, as bootstrap.sh does."""
        uv_sh = (REPO / "scripts" / "lib" / "uv.sh").read_text()
        for target in ("x86_64-pc-windows-msvc", "aarch64-pc-windows-msvc"):
            self.assertRegex(uv_sh, rf'{target}\) echo "[0-9a-f]{{64}}"', target)
        script = (REPO / "scripts" / "bootstrap.ps1").read_text()
        self.assertIn('scripts\\lib\\uv.sh', script)
        self.assertNotRegex(script, r"[0-9a-f]{64}", "no checksum of its own")
        self.assertIn("if ($got -ne $sha) { throw", script)
        self.assertRegex(script, r"pip sync [^\n]*--require-hashes[^\n]*--no-build[^\n]*requirements\.lock")
        self.assertNotIn("requirements.txt", script)
        self.assertNotRegex(script, r"(iex|Invoke-Expression)", "nothing run straight from the network")

    def test_windows_install_never_waits_forever_and_works_for_a_standard_user(self):
        """The owner's test PC (28 Oct 2026, Windows 11 build 26300 on ARM, a standard user named
        "BYB Test"): Invoke-WebRequest stalled for minutes with no error, and inside Setup uv hit
        os error 448 on its own Python junction. Every download is time-limited, and Setup leaves
        RedirectionGuard off: it runs as the student, into their own profile."""
        script = (REPO / "scripts" / "bootstrap.ps1").read_text()
        self.assertRegex(script, r"curl\.exe")
        self.assertRegex(script, r"--max-time \d+")
        for call in re.findall(r"Invoke-WebRequest [^\n]*-Uri[^\n]*", script):
            self.assertIn("-TimeoutSec", call, call)
        installer = (REPO / "packaging" / "windows" / "butler.iss").read_text()
        self.assertRegex(installer, r"(?m)^RedirectionGuard=no$")
        self.assertRegex(installer, r"(?m)^PrivilegesRequired=lowest$", "the guard is off only because Setup isn't elevated")
        self.assertNotIn("a minute or two", installer)

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
