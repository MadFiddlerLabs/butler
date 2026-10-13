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
        self.installed, self.broken_installs, self.mailed = [], set(), []
        self.settings = {"name": "Bento", "email": {"recipients": ["sam@example.com", "alex@example.com"]}}
        for name, fake in {"verify_signature": lambda p: None, "install_package": self.install,
                           "record_baseline": lambda: None, "_settings_or_none": lambda: self.settings}.items():
            p = mock.patch.object(updates, name, side_effect=fake)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(updates.mailer, "send", side_effect=lambda s, subject, text: self.mailed.append(
            (s["email"]["recipients"], subject, text)))
        p.start()
        self.addCleanup(p.stop)

    def install(self, pkg):
        content = Path(pkg).read_bytes()
        self.installed.append(content)
        if content in self.broken_installs:
            raise updates.UpdateError("Installer didn't finish (the disk is full)")
        self.version_file.write_text("v0.2.0\n" if content == b"version two" else "v0.1.0\n")

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

    def test_a_failed_check_on_the_brief_rolls_back_too(self):
        answers = iter([{"ok": False, "problem": "the morning brief didn't build: money failed (TypeError: x)"},
                        {"ok": True}])
        with mock.patch.object(updates, "post_install", side_effect=lambda prev: next(answers)):
            result = updates.apply(self.entry)
        self.assertEqual((result["status"], result["restored_ok"]), ("rolled_back", True))
        self.assertIn("the morning brief didn't build", result["problem"])
        self.assertEqual(self.mailed, [], "a clean way back needs no email: the brief says it")

    def test_an_installer_that_fails_halfway_still_goes_back(self):
        self.broken_installs.add(b"version two")
        with mock.patch.object(updates, "post_install", return_value={"ok": True}) as post:
            result = updates.apply(self.entry)
        self.assertEqual(self.installed, [b"version two", b"version one"])
        self.assertEqual((result["status"], result["restored_ok"]), ("rolled_back", True))
        self.assertIn("disk is full", result["problem"])
        self.assertEqual(post.call_count, 1, "only the way back's own check ran")

    def test_a_failed_way_back_is_reported_by_brief_and_email(self):
        self.broken_installs.add(b"version one")
        with mock.patch.object(updates, "post_install", return_value={"ok": False, "problem": "butler.render doesn't load"}):
            result = updates.apply(self.entry)
        self.assertEqual(result["status"], "rolled_back")
        self.assertFalse(result["restored_ok"])
        self.assertIn("disk is full", result["rollback_problem"])
        self.assertEqual(updates.load_state()["last_result"], result, "recorded, not lost")
        line = updates.brief_lines("Bento")[0]
        self.assertIn("going back to v0.1.0 didn't work either", line)
        self.assertIn("Download Bento again from buildyourbutler.com", line)
        self.assertEqual(len(self.mailed), 1)
        to, subject, text = self.mailed[0]
        self.assertEqual((to, subject), (["sam@example.com", "alex@example.com"], "Bento · an update needs you"))
        for part in ("v0.2.0", "butler.render doesn't load", "disk is full", "download Bento again",
                     "settings, data, projects and budget stay"):
            self.assertIn(part, text)

    def test_a_way_back_whose_own_check_fails_is_not_ok(self):
        answers = iter([{"ok": False, "problem": "new is broken"}, {"ok": False, "problem": "old is broken too"}])
        with mock.patch.object(updates, "post_install", side_effect=lambda prev: next(answers)):
            result = updates.apply(self.entry)
        self.assertEqual((result["restored"], result["restored_ok"]), ("v0.1.0", False))
        self.assertEqual(result["rollback_problem"], "old is broken too")
        self.assertEqual(len(self.mailed), 1)

    def test_no_settings_or_no_mail_still_records_the_failed_way_back(self):
        self.broken_installs.add(b"version one")
        for settings, send in ((None, None), (self.settings, OSError("smtp down"))):
            with self.subTest(settings=settings):
                self.settings, self.mailed = settings, []
                self.version_file.write_text("v0.1.0\n")     # the last subtest's way back left v0.2.0
                with mock.patch.object(updates, "post_install", return_value={"ok": False, "problem": "x"}), \
                        mock.patch.object(updates.mailer, "send", side_effect=send) as sent:
                    result = updates.apply(self.entry)
                self.assertFalse(result["restored_ok"])
                self.assertEqual(sent.call_count, 0 if settings is None else 1)

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
        updates.save_state({"checked": "2026-10-11"})       # last night's check
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

    def test_an_update_that_didnt_happen_is_said_once_then_cleared(self):
        with mock.patch.object(updates, "fetch_manifest", return_value={"version": "v0.2.0", "pkg_url": "u"}), \
                mock.patch.object(updates, "apply", side_effect=updates.UpdateError(
                    "the download didn't match its checksum, so I deleted it")):
            updates.tick(self.SETTINGS, datetime(2026, 10, 12, 3, 0))
        self.assertEqual(updates.brief_lines("Bento"), [
            "Bento couldn't update to v0.2.0 overnight: the download didn't match its checksum, so I deleted it. "
            "It tries again tomorrow."])
        updates.mark_announced()
        self.assertEqual(updates.brief_lines("Bento"), [], "said once")
        self.assertTrue(updates.load_state()["last_error"]["announced"])

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

    def test_a_failed_way_back_says_what_to_do(self):
        code, said, _ = self.run_command(result={"status": "rolled_back", "from": "v0.1.0", "restored": "v0.2.0",
                                                 "restored_ok": False, "rollback_problem": "Installer didn't finish"})
        self.assertEqual(code, 1)
        self.assertEqual(said[-1], "The new version didn't pass its check, and going back to v0.1.0 didn't work "
                                   "either (Installer didn't finish). Download Butler again from "
                                   "buildyourbutler.com and open it: your settings and data stay. "
                                   "Details are in logs/butler.log.")

    def test_a_failed_check_says_it_went_back(self):
        code, said, _ = self.run_command(result={"status": "rolled_back", "restored": "v0.1.0"})
        self.assertEqual(code, 1)
        self.assertEqual(said[-1], "The new version didn't pass its check, so I went back to v0.1.0. "
                                   "Nothing changed for you. Details are in logs/butler.log.")


class PostInstallTest(Sandbox):
    """The new code's side: dependencies, migrations, a health check, then the portal restarts."""

    def post_install(self, bootstrap=0, migration_error=None, problems=(), brief=None):
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
             mock.patch.object(updates, "health", return_value=list(problems)), \
             mock.patch.object(updates, "brief_problem", return_value=brief):
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
        result, ran = self.post_install(brief="the morning brief didn't build: money failed (TypeError: x)")
        self.assertEqual(result, {"ok": False, "problem": "the morning brief didn't build: money failed (TypeError: x)"})
        self.assertNotIn(["launchctl", "kickstart"], ran)

    def test_the_post_install_entry_point_prints_json(self):
        with mock.patch.object(updates, "run_post_install", return_value={"ok": True, "migrations": []}), \
             mock.patch("builtins.print") as printed:
            self.assertEqual(updates.main(["--post-install", "v0.1.0"]), 0)
        printed.assert_called_once_with('{"ok": true, "migrations": []}', flush=True)


class BriefCheckTest(Sandbox):
    """The new code builds today's brief; only failures the code running before didn't have count."""

    MONEY = {"where": "money failed", "error": "TypeError", "detail": "unsupported operand"}
    NEWS = {"where": "news failed", "error": "KeyError", "detail": "'regions'"}

    def problem(self, found, baseline=None):
        if baseline is not None:
            updates.UPDATES.mkdir(parents=True, exist_ok=True)
            updates.baseline_path().write_text(baseline)
        from butler import daily
        with mock.patch.object(daily, "brief_check", side_effect=found if isinstance(found, Exception)
                               else lambda: {"failures": found}):
            return updates.brief_problem()

    def test_a_clean_brief_passes(self):
        self.assertIsNone(self.problem([]))

    def test_without_a_baseline_every_failure_counts(self):
        self.assertEqual(self.problem([self.MONEY]),
                         "the morning brief didn't build: money failed (TypeError: unsupported operand)")
        self.assertIsNotNone(self.problem([self.MONEY], baseline="not json"))

    def test_a_failure_the_old_code_had_too_doesnt_stop_the_update(self):
        import json
        baseline = json.dumps({"failures": [{**self.MONEY, "detail": "another detail"}]})
        self.assertIsNone(self.problem([self.MONEY], baseline=baseline))
        self.assertEqual(self.problem([self.MONEY, self.NEWS], baseline=baseline),
                         "the morning brief didn't build: news failed (KeyError: 'regions')")

    def test_a_check_that_cant_run_fails(self):
        self.assertEqual(self.problem(RuntimeError("no settings")),
                         "the morning brief check didn't run (RuntimeError: no settings)")

    def test_the_baseline_comes_from_the_code_running_now(self):
        import json
        with mock.patch.object(updates, "_fresh_process", return_value={"failures": [self.MONEY]}) as fresh:
            updates.UPDATES.mkdir(parents=True, exist_ok=True)
            updates.record_baseline()
        fresh.assert_called_once_with(["--brief-check"], timeout=600)
        self.assertEqual(json.loads(updates.baseline_path().read_text()), {"failures": [self.MONEY]})
        with mock.patch.object(updates, "_fresh_process", return_value={"ok": False, "problem": "usage: --brief-check"}):
            updates.record_baseline()
        self.assertFalse(updates.baseline_path().exists(), "an older version without the check leaves none")

    def test_the_brief_check_entry_point_prints_json(self):
        from butler import daily
        with mock.patch.object(daily, "brief_check", return_value={"failures": []}), \
             mock.patch("builtins.print") as printed:
            self.assertEqual(updates.main(["--brief-check"]), 0)
        printed.assert_called_once_with('{"failures": []}', flush=True)


class FreshProcessTest(Sandbox):
    def test_reads_the_last_json_line(self):
        with mock.patch.object(updates.subprocess, "run", return_value=done([], 0, 'migrating\n{"ok": true}\n')):
            self.assertEqual(updates.post_install("v0.1.0"), {"ok": True})

    def test_no_answer_a_hang_or_no_python_is_a_failed_check(self):
        with mock.patch.object(updates.subprocess, "run", return_value=done([], 1, "", "Traceback: boom\n")):
            self.assertEqual(updates.post_install("v0.1.0"), {"ok": False, "problem": "Traceback: boom"})
        with mock.patch.object(updates.subprocess, "run", side_effect=subprocess.TimeoutExpired("python", 1200)):
            self.assertEqual(updates.post_install("v0.1.0"),
                             {"ok": False, "problem": "its checks took longer than 20 minutes"})
        with mock.patch.object(updates.subprocess, "run", side_effect=FileNotFoundError("no .venv")):
            self.assertIn("its Python didn't start", updates.post_install("v0.1.0")["problem"])

    def test_an_installer_that_hangs_is_an_update_error(self):
        with mock.patch.object(updates, "_run", side_effect=subprocess.TimeoutExpired("installer", 900)):
            with self.assertRaisesRegex(updates.UpdateError, "Installer didn't finish"):
                updates.install_package(self.root / "Butler.pkg")


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
