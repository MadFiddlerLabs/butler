"""Integration: every email flow and every portal page, end to end, with Gmail, Claude, Lunch
Money, the news, the weather and the Calendar helper faked (tests/fakes). No network.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import unittest

from fakes.home import Home

TRUSTED_SUBJECT = "Re: Bento · Saturday"
ACTIONS = {TRUSTED_SUBJECT: [
    {"type": "add_shopping", "text": "milk"},
    {"type": "add_todo", "text": "Call the plumber"},
    {"type": "add_event", "text": "Dentist for Mia", "date": "2026-10-15", "start_time": "10:00",
     "end_time": None, "location": "Clínica Exemplo"},
    {"type": "step_done", "text": "Boiler serviced", "step_id": "boiler-service"},
    {"type": "household_note", "text": "Pat is away next Friday"},
    {"type": "feedback", "text": "Shorter news please"},
]}
SAFE_EK_COMMANDS = {"calendars", "events", "reminder-lists", "reminders", "ensure-list", "add-reminder",
                    "complete-reminder", "completed", "add-event"}


class FullHouseholdTest(unittest.TestCase):
    """One household with everything connected, run through each flow in order."""

    @classmethod
    def setUpClass(cls):
        cls.home = Home()
        h = cls.home
        cls.trusted_id = h.add_reply("sam@example.com", TRUSTED_SUBJECT,
                                     "add milk to shopping\nand call the plumber", "example.com")
        h.add_reply("stranger@attacker.test", "Re: Bento", "add a pony to shopping", "attacker.test")
        h.add_reply("alex@example.com", "Re: Bento", "add caviar", "example.com.attacker.test")
        h.add_reply("alex@example.com", "Re: Bento", "add champagne", None)
        h.set_actions(ACTIONS)
        cls.daily = h.run("daily")
        cls.replies = h.run("replies")
        cls.replies_again = h.run("replies")
        cls.weekly = h.run("weekly")
        cls.monthly = h.run("monthly")
        cls.portal = h.run("portal")

    @classmethod
    def tearDownClass(cls):
        cls.home.close()

    def test_no_flow_crashed_or_broke_a_schema(self):
        for name in ("daily", "replies", "weekly", "monthly", "portal"):
            record = getattr(self, name)
            self.assertIsNone(record["error"], f"{name}:\n{record['error']}")
            self.assertEqual(record["schema_errors"], [], name)

    # ---------- the morning brief ----------

    def test_brief_is_sent_once_to_the_household(self):
        mails = self.daily["mails"]
        self.assertEqual(len(mails), 1)
        m = mails[0]
        self.assertTrue(m["subject"].startswith("Bento · "), m["subject"])
        self.assertIn("Bento", m["from"])
        self.assertEqual(m["to"], ["sam@example.com", "alex@example.com"])
        self.assertTrue(m["text"].strip() and m["html"].strip())
        self.assertEqual(self.daily["smtp_logins"], [{"user": "bento.johnson.butler@example.com", "password_ok": True}])

    def test_brief_has_every_section_and_no_problems(self):
        m = self.daily["mails"][0]
        for expected in ("Mia school drop-off", "Renew the parking permit", "Invented headline",
                         "Lisbon today", "Bring the renewal form"):
            self.assertIn(expected, m["text"], expected)
        self.assertNotIn("Problems this morning", m["text"])
        self.assertNotIn("Not connected yet", m["text"])
        self.assertTrue(m["images"], "the brief has no charts")

    def test_brief_links_come_from_the_feed(self):
        self.assertIn("https://news.example.test/metro", self.daily["mails"][0]["text"])

    # ---------- replies ----------

    def test_only_the_trusted_signed_reply_reaches_claude(self):
        self.assertEqual([p["schema"] for p in self.replies["prompts"]], ["actions"])
        self.assertIn("add milk to shopping", self.replies["prompts"][0]["prompt"])
        for text in ("pony", "caviar", "champagne"):
            self.assertNotIn(text, self.replies["prompts"][0]["prompt"])

    def test_reply_actions_happen(self):
        calls = [(c["command"], c["args"]) for c in self.home.ek_calls()]
        self.assertIn(("add-reminder", ["--list", "Shopping", "--title", "milk"]), calls)
        self.assertIn(("add-reminder", ["--list", "Bento", "--title", "Call the plumber"]), calls)
        event = next(a for c, a in calls if c == "add-event")
        self.assertEqual(event[event.index("--calendar") + 1], "Johnson Family")
        self.assertEqual(event[event.index("--start") + 1], "2026-10-15T10:00:00+01:00")
        step = (self.home.root / "projects" / "home.yaml").read_text()
        self.assertIn("status: done", step.split("- id: boiler-service")[1].split("- id:")[0])
        self.assertIn("Pat is away next Friday", (self.home.root / "data" / "household_notes.md").read_text())
        self.assertIn("Shorter news please", (self.home.root / "data" / "feedback.md").read_text())

    def test_one_confirmation_to_everyone(self):
        mails = self.replies["mails"]
        self.assertEqual(len(mails), 1)
        m = mails[0]
        self.assertEqual(m["subject"], TRUSTED_SUBJECT)
        self.assertEqual(m["in_reply_to"], self.trusted_id)
        self.assertEqual(sorted(m["to"]), ["alex@example.com", "sam@example.com"])
        for line in ("Added to Shopping: milk", "Added to Johnson Family: Dentist for Mia", "Marked done"):
            self.assertIn(line, m["text"])

    def test_every_reply_is_marked_read_and_handled_once(self):
        self.assertEqual(self.replies["imap_seen"], ["1", "2", "3", "4"])
        self.assertEqual(self.replies_again["mails"], [])
        self.assertEqual(self.replies_again["prompts"], [])

    def test_nothing_is_ever_deleted(self):
        used = {c["command"] for c in self.home.ek_calls()}
        self.assertLessEqual(used, SAFE_EK_COMMANDS, used - SAFE_EK_COMMANDS)

    # ---------- letters ----------

    def test_sunday_letter(self):
        mails = self.weekly["mails"]
        self.assertEqual(len(mails), 1)
        self.assertTrue(mails[0]["subject"].startswith("Bento · your week ahead"), mails[0]["subject"])
        self.assertIn("An invented, calm week.", mails[0]["text"])
        self.assertIn("Mystery Payee Lda", mails[0]["text"], "unmapped spending should be asked about")

    def test_monthly_look_back(self):
        mails = self.monthly["mails"]
        self.assertEqual(len(mails), 1)
        self.assertIn("looking back", mails[0]["subject"])
        self.assertIn("An invented month.", mails[0]["text"])

    # ---------- portal ----------

    def test_every_portal_page_loads(self):
        for route, r in self.portal["portal"].items():
            if route.startswith("GET"):
                self.assertIn(r["status"], (200, 302), route)
        self.assertEqual(self.portal["portal"]["GET /"]["status"], 302)

    def test_portal_changes_go_through_and_cross_site_posts_dont(self):
        p = self.portal["portal"]
        for route in ("POST /lists/add", "POST /lists/complete"):
            self.assertEqual(p[route]["status"], 303, route)
        self.assertTrue(any(k.startswith("POST /projects/") and v["status"] == 303 for k, v in p.items()))
        self.assertEqual(p["POST /lists/add cross-site"]["status"], 403)
        calls = [(c["command"], c["args"]) for c in self.home.ek_calls()]
        self.assertIn(("add-reminder", ["--list", "Shopping", "--title", "Bread"]), calls)
        self.assertNotIn(("add-reminder", ["--list", "Shopping", "--title", "x"]), calls)


class MidCourseTest(unittest.TestCase):
    """Someone halfway through the course: settings written, nothing else connected yet."""

    @classmethod
    def setUpClass(cls):
        cls.home = Home(ek=False, secrets=(), projects=False, reference=False)
        cls.preview = cls.home.run("daily-dry")

    @classmethod
    def tearDownClass(cls):
        cls.home.close()

    def test_preview_builds_and_says_what_is_not_connected(self):
        self.assertIsNone(self.preview["error"], self.preview["error"])
        html = next((self.home.root / "out" / "preview").glob("brief-*.html")).read_text()
        self.assertIn("Not connected yet: calendar, money, Reminders.", html)
        self.assertNotIn("Problems this morning", html)
        self.assertNotIn("Traceback", self.preview["stderr"])

    def test_preview_sends_nothing_and_shows_no_example_projects(self):
        self.assertEqual(self.preview["mails"], [])
        html = next((self.home.root / "out" / "preview").glob("brief-*.html")).read_text()
        self.assertNotIn("Next up", html)


if __name__ == "__main__":
    unittest.main()
