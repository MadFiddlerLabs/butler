"""Over-the-air updates (src/butler/updates.py) and settings migrations (src/butler/migrations.py),
with every outside effect faked: the manifest, downloads, pkgutil, spctl, Installer and the new
code's post-install. The real install-and-update runs on a Mac in macos-e2e.yml.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from butler import migrations, updates

GOOD_PKGUTIL = """Package "Butler.pkg":
   Status: signed by a developer certificate issued by Apple for distribution
   Notarization: trusted by the Apple notary service
   Certificate Chain:
    1. Developer ID Installer: Proxim.us (VB9CHJM4AN)
    2. Developer ID Certification Authority
    3. Apple Root CA
"""


def done(cmd, code=0, out="", err=""):
    return subprocess.CompletedProcess(cmd, code, out, err)


class Sandbox(unittest.TestCase):
    """A throwaway ~/Butler: updates.py's paths point into it."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "data").mkdir()
        self.version_file = self.root / ".butler-version"
        self.version_file.write_text("v0.1.0\n")
        u = self.root / "data" / "updates"
        for name, value in {"ROOT": self.root, "UPDATES": u, "STATE": u / "state.json",
                            "INSTALL_ID": u / "install-id", "VERSION_FILE": self.version_file,
                            "UNSIGNED_MARKER": self.root / ".butler-unsigned-build"}.items():
            p = mock.patch.object(updates, name, value)
            p.start()
            self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ, {"BUTLER_UPDATE_ANY_ROOT": "1"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("BUTLER_UPDATE_ALLOW_UNSIGNED", None)

    def pkg(self, name: str, content: bytes = b"a package") -> tuple[Path, str]:
        path = self.root / name
        path.write_bytes(content)
        return path, hashlib.sha256(content).hexdigest()


class VersionTest(unittest.TestCase):
    def test_parse_and_order(self):
        v = updates.parse_version
        self.assertEqual(v("v1.2.3"), (1, 2, 3, 1))
        self.assertEqual(v("1.2.3"), (1, 2, 3, 1))
        self.assertLess(v("v1.2.3-rc1"), v("v1.2.3"))
        self.assertLess(v("v0.0.0-d78f91e"), v("v0.1.0"))
        self.assertLess(v("v0.9.9"), v("v0.10.0"))
        for bad in (None, "", "main", "v1.2", "1.2.3.4", "v1.2.3 ; rm"):
            self.assertIsNone(v(bad), bad)

    def test_major(self):
        self.assertTrue(updates.is_major("v2.0.0", "v1.9.9"))
        self.assertFalse(updates.is_major("v1.10.0", "v1.9.9"))

    def test_rollout_is_stable_and_bounded(self):
        self.assertTrue(updates.in_rollout(None, "abc", "v1.0.0"))
        self.assertFalse(updates.in_rollout(0, "abc", "v1.0.0"))
        self.assertTrue(updates.in_rollout(100, "abc", "v1.0.0"))
        first = updates.in_rollout(50, "abc", "v1.0.0")
        self.assertEqual(first, updates.in_rollout(50, "abc", "v1.0.0"), "same install, same answer")
        share = sum(updates.in_rollout(30, f"id{i}", "v1.0.0") for i in range(2000)) / 2000
        self.assertAlmostEqual(share, 0.30, delta=0.05)


class DecideTest(unittest.TestCase):
    def decide(self, manifest, installed="v0.1.0", settings=None, ident="x"):
        return updates.decide(settings or {}, manifest, installed, ident)

    def test_policy(self):
        minor = {"version": "v0.2.0", "pkg_url": "u"}
        self.assertEqual(self.decide(minor).action, "install")
        self.assertEqual(self.decide({"version": "v0.1.1"}).action, "install")
        self.assertEqual(self.decide(minor, settings={"updates": "notify"}).action, "ask")
        self.assertEqual(self.decide(minor, settings={"updates": "off"}).action, "none")
        self.assertEqual(self.decide({"version": "v1.0.0"}).action, "ask", "a major always asks")
        self.assertEqual(self.decide({"version": "v0.2.0", "major": True}).action, "ask")

    def test_never_downgrades_or_repeats(self):
        self.assertEqual(self.decide({"version": "v0.1.0"}).action, "none")
        self.assertEqual(self.decide({"version": "v0.0.9"}).action, "none")
        self.assertEqual(self.decide({"available": False}).action, "none")
        self.assertEqual(self.decide({"version": "nonsense"}).action, "none")

    def test_min_version_means_a_fresh_download(self):
        d = self.decide({"version": "v0.5.0", "min_version": "v0.3.0"})
        self.assertEqual(d.action, "reinstall")

    def test_rollout_holds_some_installs_back(self):
        held = [i for i in range(200) if self.decide({"version": "v0.2.0", "rollout_percent": 10},
                                                     ident=f"id{i}").action == "none"]
        self.assertTrue(150 < len(held) < 200)


class SignatureTest(Sandbox):
    def check(self, outputs: dict, xcode=True):
        def fake(cmd, timeout=600):
            return outputs.get(cmd[0] if cmd[0] != "xcrun" else "stapler", done(cmd))
        with mock.patch.object(updates, "_run", side_effect=fake), \
                mock.patch.object(updates.shutil, "which", return_value="/usr/bin/x" if xcode else None):
            updates.verify_signature(self.root / "Butler.pkg")

    def test_a_good_package_passes(self):
        self.check({"pkgutil": done([], 0, GOOD_PKGUTIL), "spctl": done([], 0, "", "accepted\nsource=Notarized Developer ID")})

    def test_unsigned_is_refused(self):
        with self.assertRaisesRegex(updates.UpdateError, "isn't signed by Butler's publisher"):
            self.check({"pkgutil": done([], 1, 'Package "x.pkg":\n   Status: no signature')})

    def test_another_developer_is_refused(self):
        other = GOOD_PKGUTIL.replace("Proxim.us (VB9CHJM4AN)", "Mallory Ltd (ZZZZZZZZZZ)")
        with self.assertRaisesRegex(updates.UpdateError, "isn't signed"):
            self.check({"pkgutil": done([], 0, other), "spctl": done([], 0, "", "accepted")})

    def test_gatekeeper_rejection_is_refused(self):
        with self.assertRaisesRegex(updates.UpdateError, "macOS didn't approve"):
            self.check({"pkgutil": done([], 0, GOOD_PKGUTIL), "spctl": done([], 3, "", "rejected")})

    def test_bad_staple_is_refused_when_xcode_tools_exist(self):
        outputs = {"pkgutil": done([], 0, GOOD_PKGUTIL), "spctl": done([], 0, "", "accepted"),
                   "stapler": done([], 65, "", "does not have a ticket stapled")}
        with self.assertRaisesRegex(updates.UpdateError, "notarization ticket"):
            self.check(outputs)

    def test_test_flag_alone_relaxes_nothing(self):
        os.environ["BUTLER_UPDATE_ALLOW_UNSIGNED"] = "1"
        self.addCleanup(os.environ.pop, "BUTLER_UPDATE_ALLOW_UNSIGNED", None)
        self.assertFalse(updates.unsigned_allowed(), "no marker: a real install")
        (self.root / ".butler-unsigned-build").write_text("v0.0.9\n")
        self.assertFalse(updates.unsigned_allowed(), "a stale marker from another build")
        with self.assertRaisesRegex(updates.UpdateError, "isn't signed"):
            self.check({"pkgutil": done([], 1, "Status: no signature")})
        (self.root / ".butler-unsigned-build").write_text("v0.1.0\n")
        self.assertTrue(updates.unsigned_allowed(), "a test build of this exact version, with the flag")

    def test_marker_alone_relaxes_nothing(self):
        (self.root / ".butler-unsigned-build").write_text("v0.1.0\n")
        self.assertFalse(updates.unsigned_allowed())


class DownloadTest(Sandbox):
    def test_checksum_mismatch_is_deleted(self):
        src, _ = self.pkg("src.pkg")
        dest = self.root / "data" / "updates" / "Butler-v0.2.0.pkg"
        with self.assertRaisesRegex(updates.UpdateError, "checksum"):
            updates.download(str(src), dest, sha256="0" * 64)
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_name(dest.name + ".part").exists())

    def test_wrong_size_is_deleted(self):
        src, digest = self.pkg("src.pkg")
        dest = self.root / "data" / "updates" / "Butler-v0.2.0.pkg"
        with self.assertRaisesRegex(updates.UpdateError, "wrong size"):
            updates.download(str(src), dest, sha256=digest, size=3)
        self.assertFalse(dest.exists())

    def test_good_download(self):
        src, digest = self.pkg("src.pkg")
        dest = self.root / "data" / "updates" / "Butler-v0.2.0.pkg"
        updates.download(str(src), dest, sha256=digest, size=src.stat().st_size)
        self.assertEqual(dest.read_bytes(), b"a package")


class ApplyTest(Sandbox):
    def setUp(self):
        super().setUp()
        self.new, self.digest = self.pkg("new.pkg", b"version two")
        releases = self.root / "releases" / "v0.1.0"
        releases.mkdir(parents=True)
        (releases / "Butler.pkg").write_bytes(b"version one")
        os.environ["BUTLER_RELEASES_URL"] = str(self.root / "releases")
        self.addCleanup(os.environ.pop, "BUTLER_RELEASES_URL", None)
        self.entry = {"version": "v0.2.0", "pkg_url": str(self.new), "sha256": self.digest,
                      "notes": "Faster briefs. A new  Sunday chart."}
        self.installed = []
        for name, fake in {"verify_signature": lambda p: None, "install_package": self.install}.items():
            p = mock.patch.object(updates, name, side_effect=fake)
            p.start()
            self.addCleanup(p.stop)

    def install(self, pkg):
        self.installed.append(Path(pkg).read_bytes())
        self.version_file.write_text("v0.2.0\n" if Path(pkg).read_bytes() == b"version two" else "v0.1.0\n")

    def test_success(self):
        with mock.patch.object(updates, "post_install", return_value={"ok": True}):
            result = updates.apply(self.entry)
        self.assertEqual(result["status"], "updated")
        self.assertEqual(self.installed, [b"version two"])
        self.assertEqual(updates.installed_version(), "v0.2.0")
        self.assertTrue(updates.kept_package("v0.1.0").exists(), "the previous version is kept")
        self.assertTrue(updates.kept_package("v0.2.0").exists())
        self.assertEqual(updates.brief_lines("Bento"),
                         ["Bento updated overnight to v0.2.0. What's new: Faster briefs. A new Sunday chart."])
        updates.mark_announced()
        self.assertEqual(updates.brief_lines("Bento"), [], "said once")

    def test_failed_health_check_rolls_back(self):
        answers = iter([{"ok": False, "problem": "butler.render doesn't load"}, {"ok": True}])
        with mock.patch.object(updates, "post_install", side_effect=lambda prev: next(answers)):
            result = updates.apply(self.entry)
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(self.installed, [b"version two", b"version one"])
        self.assertEqual(updates.installed_version(), "v0.1.0")
        self.assertEqual(result["restored"], "v0.1.0")
        self.assertTrue(result["restored_ok"])
        self.assertFalse(updates.kept_package("v0.2.0").exists(), "the broken version isn't kept")
        self.assertIn("went back to v0.1.0", updates.brief_lines("Bento")[0])

    def test_no_rollback_copy_means_no_update(self):
        os.environ["BUTLER_RELEASES_URL"] = str(self.root / "nowhere")
        with self.assertRaisesRegex(updates.UpdateError, "couldn't keep a copy of v0.1.0"):
            updates.apply(self.entry)
        self.assertEqual(self.installed, [])

    def test_checksum_mismatch_installs_nothing(self):
        with self.assertRaisesRegex(updates.UpdateError, "checksum"):
            updates.apply({**self.entry, "sha256": "f" * 64})
        self.assertEqual(self.installed, [])

    def test_a_git_checkout_never_updates_itself(self):
        (self.root / ".git").mkdir()
        with self.assertRaisesRegex(updates.UpdateError, "git checkout"):
            updates.apply(self.entry)


class TickTest(Sandbox):
    SETTINGS = {"updates": "auto", "update_hour": 3}

    def test_checks_once_a_day_at_the_hour(self):
        manifest = {"version": "v0.2.0", "pkg_url": "u"}
        with mock.patch.object(updates, "fetch_manifest", return_value=manifest) as fetch, \
                mock.patch.object(updates, "apply") as apply:
            updates.tick(self.SETTINGS, datetime(2026, 10, 12, 2, 55))
            updates.tick(self.SETTINGS, datetime(2026, 10, 12, 3, 0))
            updates.tick(self.SETTINGS, datetime(2026, 10, 12, 3, 5))
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(apply.call_count, 1)

    def test_a_major_waits_for_the_reply(self):
        with mock.patch.object(updates, "fetch_manifest", return_value={"version": "v1.0.0", "pkg_url": "u"}), \
                mock.patch.object(updates, "apply") as apply:
            updates.tick(self.SETTINGS, datetime(2026, 10, 12, 3, 0))
            self.assertEqual(apply.call_count, 0)
            self.assertIn('Reply "update"', updates.brief_lines("Bento")[0])
            self.assertIn("Installing version v1.0.0", updates.approve())
            updates.tick(self.SETTINGS, datetime(2026, 10, 12, 9, 0))
            self.assertEqual(apply.call_count, 1, "approved: installs on the next tick")

    def test_off_never_checks(self):
        with mock.patch.object(updates, "fetch_manifest") as fetch:
            updates.tick({"updates": "off"}, datetime(2026, 10, 12, 3, 0))
        fetch.assert_not_called()

    def test_nothing_waiting(self):
        self.assertIn("no update waiting", updates.approve())


class MigrationTest(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        self.settings = d / "settings.yaml"
        self.settings.write_text("# My settings\nname: Bento  # our butler\ntimezone: Europe/Lisbon\n")
        for name, value in {"SETTINGS": self.settings, "RECORD": d / "migrations.json"}.items():
            p = mock.patch.object(migrations, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_runs_in_order_once_and_appends(self):
        before = self.settings.read_text()
        self.assertEqual(migrations.run_pending(), [1])
        after = self.settings.read_text()
        self.assertTrue(after.startswith(before.rstrip("\n")), "comments and order survive")
        self.assertIn("updates: auto", after)
        self.assertEqual(migrations.run_pending(), [], "recorded: never twice")
        self.assertEqual(self.settings.read_text(), after)

    def test_idempotent_even_without_the_record(self):
        migrations.run_pending()
        once = self.settings.read_text()
        migrations.RECORD.unlink()
        migrations.run_pending()
        self.assertEqual(self.settings.read_text(), once, "the migration checks before it changes")

    def test_numbers_are_unique_and_ordered(self):
        numbers = [n for n, _, _ in migrations.MIGRATIONS]
        self.assertEqual(numbers, sorted(set(numbers)))
        self.assertEqual(numbers[0], 1)


class CommandTest(Sandbox):
    """`butler update`, in the plain words a person at a Terminal sees."""

    MANIFEST = {"version": "v0.2.0", "url": "https://example.test/Butler.pkg", "notes": "Budget changes by reply."}

    def run_command(self, manifest=None, check=False, yes=True, typed="", result=None, fetch_error=None,
                    why_not=None):
        said, applied = [], []

        def fetch():
            if fetch_error:
                raise updates.UpdateError(fetch_error)
            return self.MANIFEST if manifest is None else manifest

        def apply(m):
            applied.append(m["version"])
            if isinstance(result, Exception):
                raise result
            return result or {"status": "updated", "to": m["version"]}

        with mock.patch.object(updates, "can_update", return_value=why_not), \
             mock.patch.object(updates, "fetch_manifest", side_effect=fetch), \
             mock.patch.object(updates, "apply", side_effect=apply), \
             mock.patch("builtins.input", return_value=typed):
            code = updates.command(check, yes, say=said.append)
        return code, said, applied

    def test_installs_a_newer_version(self):
        code, said, applied = self.run_command()
        self.assertEqual((code, applied), (0, ["v0.2.0"]))
        self.assertEqual(said, ["Version v0.2.0 is ready (you have v0.1.0). What is new: Budget changes by reply.",
                                "Downloading and checking it. This takes a minute or two…",
                                "Done. You're on version v0.2.0."])

    def test_check_only_says_how_to_install(self):
        code, said, applied = self.run_command(check=True)
        self.assertEqual((code, applied, said[-1]), (0, [], "To install it, type: butler update"))

    def test_asks_first_and_no_means_no(self):
        for typed, installs in (("no", False), ("N", False), ("", True), ("yes", True)):
            with self.subTest(typed=typed):
                code, said, applied = self.run_command(yes=False, typed=typed)
                self.assertEqual((code, bool(applied)), (0, installs))
                if not installs:
                    self.assertEqual(said[-1], "No problem. Type butler update whenever you like.")

    def test_up_to_date(self):
        for manifest in ({"version": "v0.1.0"}, {"version": "v0.0.9"}, {"version": "v0.2.0", "available": False},
                         {"version": "not a version"}, {}):
            with self.subTest(manifest=manifest):
                code, said, applied = self.run_command(manifest=manifest)
                self.assertEqual((code, said, applied), (0, ["You're up to date: version v0.1.0."], []))

    def test_too_old_for_an_update_needs_a_download(self):
        code, said, applied = self.run_command(manifest={**self.MANIFEST, "min_version": "v0.1.5"})
        self.assertEqual((code, applied), (0, []))
        self.assertEqual(said, ["Version v0.2.0 needs a fresh download from buildyourbutler.com. "
                                "Your settings and data stay."])

    def test_plain_words_when_it_cannot(self):
        code, said, _ = self.run_command(why_not="this copy is a git checkout")
        self.assertEqual((code, said), (1, ["I can't update myself: this copy is a git checkout."]))
        code, said, _ = self.run_command(fetch_error="couldn't reach the update server")
        self.assertEqual((code, said), (1, ["I couldn't reach the update server. "
                                            "Check your internet connection and try again."]))
        code, said, _ = self.run_command(result=updates.UpdateError("the download's checksum didn't match"))
        self.assertEqual((code, said[-1]), (1, "I didn't update: the download's checksum didn't match."))

    def test_a_failed_check_says_it_went_back(self):
        code, said, _ = self.run_command(result={"status": "rolled_back", "restored": "v0.1.0"})
        self.assertEqual(code, 1)
        self.assertEqual(said[-1], "The new version didn't pass its check, so I went back to v0.1.0. "
                                   "Nothing changed for you. Details are in logs/butler.log.")


class PostInstallTest(Sandbox):
    """The new code's side: dependencies, migrations, a health check, then the portal restarts."""

    def post_install(self, bootstrap=0, migration_error=None, problems=()):
        ran = []

        def run(cmd, timeout=600):
            ran.append(cmd[:2])
            if cmd[0] == "bash":
                return done(cmd, bootstrap, err="pip: no matching distribution\n")
            return done(cmd, 0)

        def migrate():
            if migration_error:
                raise migration_error
            return [3]

        env = {k: v for k, v in os.environ.items() if k != "BUTLER_UPDATE_SKIP_BOOTSTRAP"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(updates, "_run", side_effect=run), \
             mock.patch.object(updates.shutil, "which", return_value="/bin/launchctl"), \
             mock.patch.object(migrations, "run_pending", side_effect=migrate), \
             mock.patch.object(updates, "health", return_value=list(problems)):
            return updates.run_post_install(), ran

    def test_all_good_restarts_the_portal(self):
        result, ran = self.post_install()
        self.assertEqual(result, {"ok": True, "migrations": [3]})
        self.assertEqual(ran, [["bash", str(self.root / "scripts" / "bootstrap.sh")],
                               ["launchctl", "print"], ["launchctl", "kickstart"]])

    def test_each_failure_stops_it_with_the_reason(self):
        result, ran = self.post_install(bootstrap=1)
        self.assertEqual(result, {"ok": False, "problem": "dependencies didn't install (pip: no matching distribution)"})
        result, _ = self.post_install(migration_error=ValueError("settings.yaml has a tab"))
        self.assertEqual(result, {"ok": False, "problem": "a settings migration failed (settings.yaml has a tab)"})
        result, ran = self.post_install(problems=["butler.render doesn't load", "settings lost 'email'"])
        self.assertEqual(result, {"ok": False, "problem": "butler.render doesn't load; settings lost 'email'"})
        self.assertNotIn(["launchctl", "kickstart"], ran)

    def test_the_post_install_entry_point_prints_json(self):
        with mock.patch.object(updates, "run_post_install", return_value={"ok": True, "migrations": []}), \
             mock.patch("builtins.print") as printed:
            self.assertEqual(updates.main(["--post-install", "v0.1.0"]), 0)
        printed.assert_called_once_with('{"ok": true, "migrations": []}', flush=True)


class HealthTest(unittest.TestCase):
    def test_this_code_is_healthy(self):
        self.assertEqual(updates.health(), [])

    def test_a_module_that_doesnt_load_or_settings_that_lost_a_section(self):
        with mock.patch.object(updates, "HEALTH_MODULES", ("butler.config", "butler.no_such_module")), \
             mock.patch("butler.config.load_settings", return_value={"timezone": "Europe/Lisbon", "email": {}}):
            self.assertEqual(updates.health(), [
                "butler.no_such_module doesn't load (ModuleNotFoundError: No module named 'butler.no_such_module')",
                "settings lost 'household'"])
        with mock.patch("butler.config.load_settings", side_effect=OSError("settings.yaml: permission denied")):
            self.assertEqual(updates.health(), ["settings don't load (settings.yaml: permission denied)"])


if __name__ == "__main__":
    unittest.main()
