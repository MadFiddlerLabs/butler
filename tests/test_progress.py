"""Progress that marks itself: each /butler-setup step's check, the step-to-lesson mapping, the
queue that waits out being offline or signed out, and the whole thing against the fake account
API. All data is invented (the Johnsons). No real network.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import hashlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from butler import account, progress
from fakes.account_api import FakeAccountAPI
from fakes.home import Home
from test_contracts import COURSE_STEPS

REPO = Path(__file__).resolve().parents[1]
MAP = progress.course_map()
ALL_LESSONS = {lesson for entry in MAP.values() for lesson in entry["lessons"]}
SETUP_LESSONS = {"setup.name", "setup.email", "setup.town", "setup.first-brief", "setup.claude"}


class MappingTest(unittest.TestCase):
    """The one mapping. The website's engine contract check holds the other end."""

    def test_every_course_step_is_mapped_in_course_order(self):
        self.assertEqual(list(MAP), COURSE_STEPS)
        self.assertEqual(list(progress.CHECKS), COURSE_STEPS)

    def test_each_step_names_the_module_its_skill_section_names(self):
        skill = (REPO / ".claude" / "skills" / "butler-setup" / "SKILL.md").read_text()
        for step, entry in MAP.items():
            self.assertRegex(skill, rf"(?m)^## {step} \((course )?Module {entry['module']}:", step)

    def test_lesson_ids_belong_to_their_module_and_appear_once(self):
        seen = []
        for step, entry in MAP.items():
            self.assertTrue(entry["lessons"], step)
            for lesson in entry["lessons"]:
                self.assertRegex(lesson, rf"^m{entry['module']}-[a-z0-9-]+$")
                seen.append(lesson)
        self.assertEqual(len(seen), len(set(seen)))

    def test_each_step_ends_by_naming_the_next_module_and_command(self):
        """A newcomer couldn't tell where a step sat in the course. Each step's "Next:" line names
        the next step's module and its command; the last one names graduation."""
        skill = (REPO / ".claude" / "skills" / "butler-setup" / "SKILL.md").read_text()
        sections = re.split(r"(?m)^## ", skill)
        body = {s.split(" ", 1)[0]: s for s in sections}
        for step, after in zip(COURSE_STEPS, COURSE_STEPS[1:]):
            self.assertIn("\nNext: Module ", body[step], step)
            self.assertIn(f"Module {MAP[after]['module']}, ", body[step], step)
            self.assertIn(f"(`/butler-setup {after}`)", body[step], step)
        self.assertIn("\nNext: Module 12, Graduation", body[COURSE_STEPS[-1]])

    def test_every_step_has_its_module_page_in_the_sites_shape(self):
        """/butler-setup offers each module's page before the step. The address comes from this
        file (the contract check compares its slugs with the course), never from Claude's memory."""
        data = json.loads(progress.MAP_FILE.read_text())
        self.assertEqual(data["site"], "https://buildyourbutler.com")
        modules = progress.course_modules()
        self.assertEqual(sorted(modules), list(range(13)))
        for n, m in modules.items():
            self.assertRegex(m["slug"], r"^[a-z0-9]+(-[a-z0-9]+)*$", n)
            self.assertTrue(m["title"] and m["title"] == m["title"].strip(), n)
        for step, entry in MAP.items():
            self.assertRegex(entry["url"], r"^https://buildyourbutler\.com/course/[a-z0-9-]+/$", step)
            self.assertEqual(entry["url"], progress.module_url(entry["module"]), step)
        self.assertEqual(MAP["calendar"]["url"], "https://buildyourbutler.com/course/show-your-butler-your-calendar/")

    def test_the_skill_titles_match_the_module_titles(self):
        skill = (REPO / ".claude" / "skills" / "butler-setup" / "SKILL.md").read_text()
        modules = progress.course_modules()
        for step, entry in MAP.items():
            self.assertRegex(skill, rf"(?m)^## {step} \((course )?Module {entry['module']}: "
                                    rf"{re.escape(modules[entry['module']]['title'])}\)$", step)

    def test_the_skill_offers_the_course_page_first_and_waits(self):
        skill = (REPO / ".claude" / "skills" / "butler-setup" / "SKILL.md").read_text()
        rule = skill.split("**Start each step from its course page.**")[1].split("\n- ")[0]
        for words in ("`url` in `src/butler/course_progress.json`", "Open it in your browser now? (yes / no)",
                      "Yes is the default", "run `open <url>`", "wait for them", "On no, carry on"):
            self.assertIn(words, " ".join(rule.split()), words)

    def test_the_skill_runs_tailscale_by_its_full_path(self):
        """The Mac app puts no `tailscale` on the PATH (council #213)."""
        skill = (REPO / ".claude" / "skills" / "butler-setup" / "SKILL.md").read_text()
        self.assertNotRegex(skill, r"`tailscale ")
        self.assertIn("`/Applications/Tailscale.app/Contents/MacOS/Tailscale serve --bg 8787`", " ".join(skill.split()))
        from butler import portal
        self.assertEqual(portal.TAILSCALE, "/Applications/Tailscale.app/Contents/MacOS/Tailscale")

    def test_the_skill_lets_them_skip_and_labels_questions(self):
        skill = (REPO / ".claude" / "skills" / "butler-setup" / "SKILL.md").read_text()
        self.assertIn("offer to skip it", skill)
        self.assertIn("Never press\n  for personal details", skill)
        self.assertIn('"Setup Module · Where you live:"', skill)

    def test_the_skill_tells_claude_to_confirm_each_step(self):
        skill = (REPO / ".claude" / "skills" / "butler-setup" / "SKILL.md").read_text()
        self.assertTrue("bin/butler progress" in skill, "the skill never runs bin/butler progress")
        table = skill.split("## Where am I?")[1]
        rows = re.findall(r"(?m)^\|.*\| ([a-z]+) \|$", table)
        self.assertEqual(rows, COURSE_STEPS, "the Where am I? table must list every step, in order")


class Setup:
    """A throwaway Butler folder, built up one step at a time. Keychain and launchd are fakes."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="butler-progress-"))
        (self.root / "config").mkdir()
        shutil.copy(REPO / "config" / "settings.example.yaml", self.root / "config" / "settings.example.yaml")
        self.secrets: set[str] = set()
        self.loaded = False
        self.folder = progress.Folder(self.root, lambda s: s in self.secrets, lambda: self.loaded)

    def settings(self, **changes) -> None:
        """Merges changes into settings.yaml. A double underscore nests: household__about."""
        path = self.root / "config" / "settings.yaml"
        s = yaml.safe_load(path.read_text()) if path.exists() else {}
        for key, value in changes.items():
            *parents, last = key.split("__")
            node = s
            for p in parents:
                node = node.setdefault(p, {})
            node[last] = value
        path.write_text(yaml.safe_dump(s))

    def touch(self, rel: str, text: str = "x") -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def module_zero(self) -> None:
        """Where the guided first run leaves a household: named, town, adults, a preview brief."""
        example = yaml.safe_load((REPO / "config" / "settings.example.yaml").read_text())
        self.settings(name="Bento", weather={"place": "Lisbon"}, household__adults=[{"name": "Sam"}],
                      news=example["news"])
        self.touch("out/preview/brief-2026-10-11.html")

    def passed(self) -> list[str]:
        return progress.passed(self.folder)

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class ChecksTest(unittest.TestCase):
    """A step is done only when its own check passes: never because it started, never half way."""

    def setUp(self):
        self.s = Setup()
        self.addCleanup(self.s.close)

    def test_a_fresh_folder_has_done_nothing(self):
        self.assertEqual(self.s.passed(), [])

    def test_module_zero_alone_is_not_module_two(self):
        self.s.module_zero()
        self.assertNotIn("install", self.s.passed())

    def test_install_needs_the_household_own_news(self):
        self.s.module_zero()
        self.s.settings(news={"regions": [{"key": "local", "label": "Lisbon",
                                           "feeds": [{"name": "The Lisbon Post (invented)", "url": "https://example.com/rss"}]}]})
        self.assertIn("install", self.s.passed())
        self.s.settings(name="Butler")
        self.assertNotIn("install", self.s.passed(), "the placeholder name means it was never named")

    def test_calendar_needs_the_helper_and_chosen_calendars(self):
        self.s.touch("bin/butler-ek")
        self.assertNotIn("calendar", self.s.passed(), "the helper alone: permission asked, nothing picked")
        self.s.settings(calendar={"include": ["Johnson Family"]})
        self.assertIn("calendar", self.s.passed())

    def test_calendar_from_google_needs_the_sign_in_and_chosen_calendars(self):
        self.s.settings(calendar={"source": "google", "include": ["Johnson Family"]})
        self.assertNotIn("calendar", self.s.passed(), "Google chosen but never signed in")
        self.s.touch("bin/butler-ek")
        self.assertNotIn("calendar", self.s.passed(), "Apple's helper doesn't count for Google")
        self.s.secrets.add("butler-google")
        self.assertIn("calendar", self.s.passed())

    def test_brain_needs_the_key_and_the_household(self):
        self.s.secrets.add("butler-anthropic")
        self.assertNotIn("brain", self.s.passed())
        self.s.settings(household__about="   ")
        self.assertNotIn("brain", self.s.passed())
        self.s.settings(household__about="Sam and Alex live in Lisbon (invented).")
        self.assertIn("brain", self.s.passed())

    def test_inbox_needs_a_sent_brief_and_the_schedule(self):
        self.s.secrets.add("butler-gmail")
        self.s.settings(email={"sender": "bento@example.com", "recipients": ["sam@example.com"]})
        self.s.touch("out/preview/brief-2026-10-11.html")
        self.s.loaded = True
        self.assertNotIn("inbox", self.s.passed(), "a preview isn't a real email")
        self.s.touch("out/brief-2026-10-12.html")
        self.assertIn("inbox", self.s.passed())
        self.s.loaded = False
        self.assertNotIn("inbox", self.s.passed(), "no schedule, no daily brief")

    def test_replies_needs_a_handled_reply(self):
        self.s.touch("data/processed_replies.txt", "")
        self.assertNotIn("replies", self.s.passed())
        self.s.touch("data/processed_replies.txt", "<reply-1@example.com>\n")
        self.assertIn("replies", self.s.passed())

    def test_money_needs_the_token_the_map_and_the_budget(self):
        self.s.secrets.add("butler-lunchmoney")
        self.s.touch("config/category_map.yaml")
        self.assertNotIn("money", self.s.passed())
        self.s.touch("budget/budget.xlsx")
        self.assertIn("money", self.s.passed())

    def test_projects_needs_a_project_not_the_reference_file(self):
        self.s.touch("projects/reference.local.yaml")
        self.assertNotIn("projects", self.s.passed())
        self.s.touch("projects/boiler.yaml")
        self.assertIn("projects", self.s.passed())

    def test_letters_needs_both_letters(self):
        self.s.touch("out/preview/weekly-2026-41.html")
        self.assertNotIn("letters", self.s.passed())
        self.s.touch("out/monthly-2026-09.html")
        self.assertIn("letters", self.s.passed())

    def test_phone_needs_the_portal_address(self):
        self.s.settings(portal={"url": ""})
        self.assertNotIn("phone", self.s.passed())
        self.s.settings(portal={"url": "https://bento.tail1234.ts.net"})
        self.assertIn("phone", self.s.passed())

    def test_a_check_that_breaks_has_not_passed(self):
        (self.s.root / "config" / "settings.yaml").write_text("{ not yaml")
        self.s.folder.has_secret = mock.Mock(side_effect=OSError("no security command"))
        self.assertEqual(self.s.passed(), [])


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.s = Setup()
        self.addCleanup(self.s.close)
        self.state = progress.load(self.s.root)

    def test_a_passing_step_queues_its_whole_module_once(self):
        self.s.touch("projects/boiler.yaml")
        self.assertEqual(progress.detect(self.state, self.s.folder, "2026-10-11T09:00:00Z"), ["projects"])
        self.assertEqual(list(self.state["queued"]), MAP["projects"]["lessons"])
        self.assertTrue(all(q == {"status": "done", "at": "2026-10-11T09:00:00Z"} for q in self.state["queued"].values()))
        self.assertEqual(progress.detect(self.state, self.s.folder, "2026-10-11T09:05:00Z"), [])
        self.assertEqual(len(self.state["queued"]), len(MAP["projects"]["lessons"]))

    def test_done_wins_over_started_and_nothing_sent_goes_again(self):
        progress.queue(self.state, "setup.claude", "started", "t1")
        progress.queue(self.state, "setup.claude", "done", "t2")
        progress.queue(self.state, "setup.claude", "started", "t3")
        self.assertEqual(self.state["queued"]["setup.claude"], {"status": "done", "at": "t2"})
        self.state["reported"]["setup.town"] = {"status": "done", "at": "t0", "sent_at": "t0"}
        progress.queue(self.state, "setup.town", "done", "t4")
        self.assertNotIn("setup.town", self.state["queued"])

    def test_the_state_file_round_trips_and_survives_damage(self):
        progress.queue(self.state, "setup.town", "done", "t1")
        progress.save(self.state, self.s.root)
        self.assertEqual(progress.load(self.s.root)["queued"], {"setup.town": {"status": "done", "at": "t1"}})
        (self.s.root / progress.STATE_FILE).write_text("{ broken")
        self.assertEqual(progress.load(self.s.root)["queued"], {})


class FlushTest(unittest.TestCase):
    """Sending what's queued to the fake account API."""

    def setUp(self):
        self.api = FakeAccountAPI(known_lessons=ALL_LESSONS | SETUP_LESSONS).start()
        self.addCleanup(self.api.stop)
        self.token = "t" * 43
        self.api.sessions[hashlib.sha256(self.token.encode()).hexdigest()] = "sam@example.com"
        patcher = mock.patch.object(account, "API", self.api.url + "/api")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = progress.load(Path(tempfile.mkdtemp()))
        for lesson in MAP["letters"]["lessons"]:
            progress.queue(self.state, lesson, "done", "2026-10-11T09:00:00Z")

    def sent(self) -> list[str]:
        return [p["lesson"] for p in self.api.progress]

    def test_signed_in_and_online_sends_everything_with_its_time(self):
        self.assertIsNone(progress.flush(self.state, self.token, "2026-10-11T09:05:00Z"))
        self.assertEqual(self.sent(), MAP["letters"]["lessons"])
        self.assertEqual({p["at"] for p in self.api.progress}, {"2026-10-11T09:00:00Z"})
        self.assertEqual(self.state["queued"], {})
        self.assertEqual(self.state["reported"]["m9-monthly-look-back"]["sent_at"], "2026-10-11T09:05:00Z")

    def test_only_lesson_ids_statuses_and_times_leave_the_mac(self):
        progress.flush(self.state, self.token, "2026-10-11T09:05:00Z")
        bodies = [body for method, path, body in self.api.calls if path == "/api/progress"]
        self.assertTrue(bodies)
        for body in bodies:
            self.assertEqual(set(body), {"lesson", "status", "at"})

    def test_signed_out_keeps_everything(self):
        self.assertEqual(progress.flush(self.state, None, "2026-10-11T09:05:00Z"), "signed out")
        self.assertEqual(len(self.state["queued"]), 3)
        self.assertEqual(self.api.calls, [])

    def test_a_revoked_session_keeps_everything(self):
        self.assertEqual(progress.flush(self.state, "r" * 43, "2026-10-11T09:05:00Z"), "signed out")
        self.assertEqual(len(self.state["queued"]), 3)

    def test_offline_keeps_everything_and_the_tick_waits_before_trying_again(self):
        with mock.patch.object(account, "API", "http://127.0.0.1:9/api"):
            self.assertEqual(progress.flush(self.state, self.token, "2026-10-11T09:05:00Z"), "offline")
        self.assertEqual(len(self.state["queued"]), 3)
        calls = len(self.api.calls)
        self.assertEqual(progress.flush(self.state, self.token, "2026-10-11T09:10:00Z"), "offline")
        self.assertEqual(len(self.api.calls), calls, "the next tick doesn't try again straight away")
        self.assertIsNone(progress.flush(self.state, self.token, "2026-10-11T09:40:00Z"))
        self.assertEqual(self.sent(), MAP["letters"]["lessons"])

    def test_a_butler_command_tries_at_once(self):
        with mock.patch.object(account, "API", "http://127.0.0.1:9/api"):
            progress.flush(self.state, self.token, "2026-10-11T09:05:00Z")
        self.assertIsNone(progress.flush(self.state, self.token, "2026-10-11T09:06:00Z", force=True))
        self.assertEqual(len(self.sent()), 3)

    def test_a_server_error_stops_and_keeps_the_rest(self):
        self.api.progress_down = True
        self.assertEqual(progress.flush(self.state, self.token, "2026-10-11T09:05:00Z"), "offline")
        self.assertEqual(len(self.state["queued"]), 3)

    def test_a_lesson_the_server_refuses_never_blocks_the_others(self):
        progress.queue(self.state, "m99-not-a-lesson", "done", "2026-10-11T09:00:00Z")
        state = {**self.state, "queued": {"m99-not-a-lesson": self.state["queued"].pop("m99-not-a-lesson"),
                                          **self.state["queued"]}}
        self.assertIsNone(progress.flush(state, self.token, "2026-10-11T09:05:00Z"))
        self.assertEqual(self.sent(), MAP["letters"]["lessons"])
        self.assertEqual(state["queued"], {})


class SyncTest(unittest.TestCase):
    """sync() and report(): detect, queue, send, never raise."""

    def setUp(self):
        self.s = Setup()
        self.addCleanup(self.s.close)
        self.api = FakeAccountAPI(known_lessons=ALL_LESSONS | SETUP_LESSONS).start()
        self.addCleanup(self.api.stop)
        self.token = "s" * 43
        self.api.sessions[hashlib.sha256(self.token.encode()).hexdigest()] = "sam@example.com"
        for name, value in (("API", self.api.url + "/api"), ("session", lambda: None)):
            patcher = mock.patch.object(account, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_finished_offline_then_sent_once_signed_in(self):
        self.s.touch("bin/butler-ek")
        self.s.settings(calendar={"include": ["Johnson Family"]})
        state, problem = progress.sync(self.s.root, self.s.folder)
        self.assertEqual(problem, "signed out")
        self.assertEqual(list(state["queued"]), MAP["calendar"]["lessons"])
        with mock.patch.object(account, "session", return_value=self.token):
            state, problem = progress.sync(self.s.root, self.s.folder)
        self.assertIsNone(problem)
        self.assertEqual([p["lesson"] for p in self.api.progress], MAP["calendar"]["lessons"])
        when = state["steps"]["calendar"]
        self.assertEqual({p["at"] for p in self.api.progress}, {when}, "the time the check passed, not the time sent")
        with mock.patch.object(account, "session", return_value=self.token):
            progress.sync(self.s.root, self.s.folder)
        self.assertEqual(len(self.api.progress), len(MAP["calendar"]["lessons"]), "never twice")

    def test_report_sends_a_setup_lesson_or_queues_it(self):
        progress.report("setup.town", root=self.s.root)
        self.assertEqual(list(progress.load(self.s.root)["queued"]), ["setup.town"])
        with mock.patch.object(account, "session", return_value=self.token):
            progress.report("setup.first-brief", root=self.s.root)
        self.assertEqual([p["lesson"] for p in self.api.progress], ["setup.town", "setup.first-brief"])

    def test_sync_never_raises(self):
        with mock.patch.object(progress, "detect", side_effect=RuntimeError("boom")):
            state, problem = progress.sync(self.s.root, self.s.folder)
        self.assertEqual(problem, "offline")
        with mock.patch.object(account, "session", side_effect=OSError("no security command")):
            self.assertEqual(progress.sync(self.s.root, self.s.folder)[1], None)
        with mock.patch.object(progress, "save", side_effect=OSError("disk full")):
            progress.report("setup.town", root=self.s.root)

    def test_lines_say_what_the_course_knows(self):
        state = progress.load(self.s.root)
        state["steps"] = {"calendar": "2026-10-11T09:00:00Z", "brain": "2026-10-11T10:00:00Z"}
        state["reported"] = {l: {"status": "done", "at": "x", "sent_at": "2026-10-11T09:05:00Z"}
                             for l in MAP["calendar"]["lessons"] + ["setup.town"]}
        state["queued"] = {l: {"status": "done", "at": "x"} for l in MAP["brain"]["lessons"]}
        with mock.patch.object(progress, "plain_output", return_value=False):
            text = "\n".join(progress.lines(state, "signed out"))
        self.assertIn("✓ Module 3 · /butler-setup calendar: sent 11 October", text)
        self.assertIn("… Module 4 · /butler-setup brain: done, waiting to send", text)
        self.assertIn("· Module 5 · /butler-setup inbox: not done yet", text)
        # For a screen reader, the same in words only.
        with mock.patch.object(progress, "plain_output", return_value=True):
            plain = "\n".join(progress.lines(state, "signed out"))
        self.assertIn("  Module 3, /butler-setup calendar: sent 11 October", plain)
        self.assertIn("  Module 4, /butler-setup brain: done, waiting to send", plain)
        self.assertIn("  Module 5, /butler-setup inbox: not done yet", plain)
        self.assertNotRegex(plain, "[✓…·]")
        self.assertIn("Also sent: setup.town", text)
        self.assertIn("type: butler setup", text)
        self.assertIn("couldn't reach", "\n".join(progress.lines(state, "offline")))


class TickTest(unittest.TestCase):
    def test_the_tick_syncs_progress_and_carries_on(self):
        from butler import tick
        with mock.patch.object(tick.replies, "process"), mock.patch.object(tick.updates, "tick"), \
             mock.patch.object(tick, "load_settings", return_value={"timezone": "Europe/Lisbon"}), \
             mock.patch.object(tick, "_weekly_if_due"), mock.patch.object(tick, "_monthly_if_due"), \
             mock.patch.object(tick.daily, "main") as daily, \
             mock.patch.object(tick.progress, "sync") as sync, \
             mock.patch.object(tick.logging, "basicConfig"), \
             mock.patch.object(tick, "ROOT", Path(tempfile.mkdtemp())) as root:
            (root / "config").mkdir()
            (root / "config" / "settings.yaml").write_text("name: Bento\n")
            tick.main()
        sync.assert_called_once_with()
        daily.assert_called_once()

    def test_without_the_households_settings_the_tick_does_nothing(self):
        """After "Start fresh" moved the settings to a backup, the schedule may still run. It must
        never fall back to the invented example household and send its emails."""
        from butler import tick
        with mock.patch.object(tick.replies, "process") as replies, mock.patch.object(tick.updates, "tick"), \
             mock.patch.object(tick.mailer, "flush") as flush, \
             mock.patch.object(tick.daily, "main") as daily, mock.patch.object(tick.progress, "sync"), \
             mock.patch.object(tick.logging, "basicConfig"), \
             mock.patch.object(tick, "ROOT", Path(tempfile.mkdtemp())):
            tick.main()
        replies.assert_not_called()
        flush.assert_not_called()
        daily.assert_not_called()


class JohnsonsIntegrationTest(unittest.TestCase):
    """The invented Johnsons' whole household, run through each flow, then a progress sync
    against the fake account API, in a subprocess like launchd's tick."""

    @classmethod
    def setUpClass(cls):
        cls.api = FakeAccountAPI(known_lessons=ALL_LESSONS | SETUP_LESSONS).start()
        cls.home = h = Home()
        token = "j" * 43
        cls.api.sessions[hashlib.sha256(token.encode()).hexdigest()] = "sam@example.com"
        secrets = json.loads((h.root / "fakes" / "secrets.json").read_text())
        (h.root / "fakes" / "secrets.json").write_text(json.dumps({**secrets, "butler-account": token}))
        launchctl = h.root / "fakebin" / "launchctl"
        launchctl.write_text("#!/bin/sh\nprintf 'PID\\tStatus\\tLabel\\n-\\t0\\tcom.buildyourbutler.butler\\n'\n")
        launchctl.chmod(0o755)
        h.add_reply("sam@example.com", "Re: Bento · Saturday", "add milk to shopping", "example.com")
        h.set_actions({"Re: Bento · Saturday": [{"type": "add_shopping", "text": "milk"}]})
        for flow in ("daily", "replies", "weekly", "monthly"):
            record = h.run(flow)
            assert record["error"] is None, record["error"]
        env = {"BUTLER_API": cls.api.url + "/api"}
        cls.offline = h.run("progress", env={"BUTLER_API": "http://127.0.0.1:9/api"})
        cls.first = h.run("progress", env=env)
        cls.again = h.run("progress", env=env)

    @classmethod
    def tearDownClass(cls):
        cls.api.stop()
        cls.home.close()

    def test_offline_it_queues(self):
        self.assertIsNone(self.offline["error"])
        self.assertEqual(self.offline["progress"]["problem"], "offline")
        self.assertTrue(self.offline["progress"]["state"]["queued"])

    def test_each_finished_step_reports_its_module(self):
        self.assertIsNone(self.first["error"], self.first["error"])
        self.assertIsNone(self.first["progress"]["problem"])
        steps = set(self.first["progress"]["state"]["steps"])
        # The Johnsons keep the example news and have no category map of their own, so Modules 2
        # and 7 aren't done; everything else is.
        self.assertEqual(steps, {"calendar", "brain", "inbox", "replies", "projects", "letters", "phone"})
        expected = [l for step in COURSE_STEPS if step in steps for l in MAP[step]["lessons"]]
        self.assertEqual(sorted(p["lesson"] for p in self.api.progress), sorted(expected))
        self.assertTrue(all(p["status"] == "done" for p in self.api.progress))

    def test_nothing_is_sent_twice(self):
        self.assertEqual(len(self.api.progress), len({p["lesson"] for p in self.api.progress}))

    def test_nothing_from_the_household_reaches_the_account(self):
        sent = json.dumps([c for c in self.api.calls if c[1] == "/api/progress"])
        for word in ("Johnson", "Lisbon", "Bento", "milk", "@"):
            self.assertNotIn(word, sent)
        state = (self.home.root / "data" / "progress.json").read_text()
        for word in ("Johnson", "Lisbon", "Bento", "milk", "@"):
            self.assertNotIn(word, state)


if __name__ == "__main__":
    unittest.main()
