"""Each reply action does what its confirmation says, on real project files in a throwaway folder:
timed calendar events, a step's check, status and note, a practice score, an update approval;
and how a batch of replies runs (a dry run sends and marks nothing; one failing action doesn't
stop the rest). test_security.py holds the allowlist itself; test_flows.py the whole flow through
the fakes. The Johnsons and everything they say are invented.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

import yaml

from butler import inbox, practice, projects, replies

REPO = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 9)
SETTINGS = {"timezone": "Europe/Lisbon",
            "household": {"about": "The Johnsons, invented.",
                          "adults": [{"name": "Sam", "emails": ["sam@example.com"]},
                                     {"name": "Alex", "emails": ["alex@example.com"]}]},
            "email": {"sender": "bento@example.com", "recipients": ["sam@example.com", "alex@example.com"],
                      "trusted_senders": ["sam@example.com", "alex@example.com"]},
            "reminders": {"todo_list": "Bento", "shopping_list": "Shopping"},
            "calendar": {"write_to": "Johnson Family"},
            "score_tracker": {"person": "Mia", "name": "driving theory test", "out_of": 30, "pass_score": 27}}


def action(kind, text, **fields):
    base = {"type": kind, "text": text, "step_id": None, "status": None, "date": None, "start_time": None,
            "end_time": None, "location": None, "category": None, "waiting_on": None, "amount": None}
    return {**base, **fields}


class InAThrowawayHome(unittest.TestCase):
    """Every file a reply can write lives in a temp folder; the Calendar helper is recorded."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "projects").mkdir()
        for example in (REPO / "examples" / "projects").glob("*.yaml"):
            shutil.copy(example, self.root / "projects" / example.name)
        data = self.root / "data"
        self.ek = []
        for p in (mock.patch.object(projects, "PROJECTS_DIR", self.root / "projects"),
                  mock.patch.object(projects, "STEP_LOG", data / "step_updates.jsonl"),
                  mock.patch.object(replies, "NOTES", data / "household_notes.md"),
                  mock.patch.object(replies, "FEEDBACK", data / "feedback.md"),
                  mock.patch.object(replies, "PROCESSED", data / "processed_replies.txt"),
                  mock.patch.object(replies, "ATTEMPTS", data / "reply_attempts.json"),
                  mock.patch.object(practice, "SCORES", data / "practice_scores.jsonl"),
                  mock.patch.object(replies, "_ek", side_effect=lambda *a: self.ek.append(list(a)) or {})):
            p.start()
            self.addCleanup(p.stop)

    def do(self, kind, text, **fields):
        return replies.perform(SETTINGS, action(kind, text, **fields), "Sam", TODAY)

    def step(self, step_id) -> dict:
        for path in (self.root / "projects").glob("*.yaml"):
            for s in yaml.safe_load(path.read_text())["steps"]:
                if s["id"] == step_id:
                    return s
        raise KeyError(step_id)

    def logged(self) -> list[dict]:
        return projects.updates()


class CalendarEvents(InAThrowawayHome):
    def option(self, name):
        args = self.ek[-1]
        return args[args.index(f"--{name}") + 1]

    def test_a_timed_event_in_the_household_zone(self):
        said = self.do("add_event", "Dentist for Mia", date="2026-10-15", start_time="10:00", end_time="11:30",
                       location="Rua Augusta 1")
        self.assertEqual(self.ek[-1][0], "add-event")
        # Lisbon is on summer time (UTC+1) until 25 October.
        self.assertEqual((self.option("start"), self.option("end"), self.option("all-day")),
                         ("2026-10-15T10:00:00+01:00", "2026-10-15T11:30:00+01:00", "0"))
        self.assertEqual((self.option("location"), self.option("notes")),
                         ("Rua Augusta 1", "Added by reply from Sam"))
        self.assertEqual(said, "Added to Johnson Family: Dentist for Mia, Thu 15 Oct 10:00–11:30")

    def test_an_event_without_an_end_lasts_an_hour(self):
        said = self.do("add_event", "Parents' evening", date="2026-11-03", start_time="18:30")
        self.assertEqual((self.option("start"), self.option("end"), self.option("location")),
                         ("2026-11-03T18:30:00+00:00", "2026-11-03T19:30:00+00:00", ""))
        self.assertTrue(said.endswith("Tue 3 Nov 18:30–19:30"), said)


class ProjectSteps(InAThrowawayHome):
    def test_a_check_logs_today_without_finishing_the_step(self):
        said = self.do("step_checked", "boiler looked fine", step_id="boiler-service")
        self.assertEqual(said, "Logged today's check (Home): boiler looked fine")
        s = self.step("boiler-service")
        self.assertEqual((s["last_done"], s.get("status")), (TODAY, None))
        self.assertEqual(self.logged(), [])

    def test_waiting_on_someone_until_a_date(self):
        said = self.do("step_status", "plumber is ordering a part", step_id="boiler-service",
                       status="waiting", waiting_on="the plumber", date="2026-10-20")
        self.assertEqual(said, "Set to waiting, waiting on the plumber, back on 20 October (Home): "
                               "plumber is ordering a part")
        s = self.step("boiler-service")
        self.assertEqual((s["status"], s["waiting_on"], s["chase_after"]),
                         ("waiting", "the plumber", date(2026, 10, 20)))
        self.assertEqual([(u["step"], u["who"], u["text"]) for u in self.logged()],
                         [("boiler-service", "Sam", "plumber is ordering a part")])

    def test_paused_keeps_no_one_to_chase(self):
        said = self.do("step_status", "after the holidays", step_id="smoke-alarm", status="paused",
                       waiting_on="nobody", date="2026-12-01")
        s = self.step("smoke-alarm")
        self.assertEqual(s["status"], "paused")
        self.assertNotIn("chase_after", s)
        self.assertEqual(said, "Set to paused, waiting on nobody (Home): after the holidays")

    def test_only_the_three_statuses_a_reply_may_set(self):
        before = {p.name: p.read_text() for p in (self.root / "projects").glob("*.yaml")}
        for status in ("done", "deleted", None):
            with self.subTest(status), self.assertRaises(ValueError):
                self.do("step_status", "x", step_id="boiler-service", status=status)
        self.assertEqual({p.name: p.read_text() for p in (self.root / "projects").glob("*.yaml")}, before)
        self.assertEqual(self.logged(), [])

    def test_a_note_is_logged_and_changes_nothing_else(self):
        before = {p.name: p.read_text() for p in (self.root / "projects").glob("*.yaml")}
        said = self.do("step_note", "landlord wants the certificate by email", step_id="boiler-service")
        self.assertEqual(said, "Noted on the step: landlord wants the certificate by email")
        self.assertEqual({p.name: p.read_text() for p in (self.root / "projects").glob("*.yaml")}, before)
        self.assertEqual([u["text"] for u in self.logged()], ["landlord wants the certificate by email"])

    def test_a_note_on_a_step_that_doesnt_exist_is_refused(self):
        with self.assertRaises(KeyError):
            self.do("step_note", "x", step_id="no-such-step")
        self.assertEqual(self.logged(), [])


class TickingOff(InAThrowawayHome):
    """'done: …' ticks off a to-do or shopping item (#38): the brief promises it."""

    def setUp(self):
        super().setUp()
        lists = {"Bento": [{"id": "r1", "title": "Renew the parking permit"},
                           {"id": "r2", "title": "Book the boiler service"},
                           {"id": "r4", "title": "Book the car service"}],
                 "Shopping": [{"id": "r3", "title": "Olive oil"}]}

        def ek(*args):
            self.ek.append(list(args))
            return lists[args[2]] if args[0] == "reminders" else {}
        replies._ek.side_effect = ek

    def completed(self):
        return [a for a in self.ek if a[0] == "complete-reminder"]

    def test_the_named_item_is_completed_on_either_list(self):
        self.assertEqual(self.do("todo_done", "olive oil"), "Ticked off Shopping in Reminders: Olive oil")
        self.assertEqual(self.do("todo_done", "the parking permit"),
                         "Ticked off Bento in Reminders: Renew the parking permit")
        self.assertEqual(self.completed(), [["complete-reminder", "--id", "r3"], ["complete-reminder", "--id", "r1"]])

    def test_an_exact_title_wins_over_a_looser_match(self):
        self.assertEqual(self.do("todo_done", "Book the boiler service."), "Ticked off Bento in Reminders: Book the boiler service")
        self.assertEqual(self.completed(), [["complete-reminder", "--id", "r2"]])

    def test_nothing_or_several_matching_ticks_nothing(self):
        with self.assertRaisesRegex(ValueError, "nothing open"):
            self.do("todo_done", "walk the dog")
        with self.assertRaisesRegex(ValueError, "Book the boiler service, Book the car service"):
            self.do("todo_done", "book the")
        with self.assertRaisesRegex(ValueError, "nothing open"):
            self.do("todo_done", "  ")
        self.assertEqual(self.completed(), [])

    def test_on_google_the_confirmation_says_google_tasks(self):
        google = {**SETTINGS, "calendar": {"write_to": "Johnson Family", "source": "google"}}
        said = replies.perform(google, action("todo_done", "olive oil"), "Sam", TODAY)
        self.assertEqual(said, "Ticked off Shopping in Google Tasks: Olive oil")

    def test_the_brief_promises_only_what_a_reply_can_do(self):
        self.assertIn("todo_done", replies.ACTION_SCHEMA["properties"]["actions"]["items"]["properties"]["type"]["enum"])
        from butler import render
        self.assertIn('reply "done: …"', Path(render.__file__).read_text())


class OtherActions(InAThrowawayHome):
    def test_a_practice_score(self):
        said = self.do("practice_score", "Mia got 27 / 30 today")
        self.assertEqual(said, "Logged Mia's driving theory test: 27/30")
        rows = [json.loads(l) for l in practice.SCORES.read_text().splitlines()]
        self.assertEqual(rows, [{"date": "2026-10-09", "who": "Mia", "score": 27, "total": 30}])

    def test_a_practice_score_needs_a_score_and_a_tracker(self):
        with self.assertRaises(ValueError):
            self.do("practice_score", "Mia did well")
        with self.assertRaises(ValueError):
            replies.perform({**SETTINGS, "score_tracker": None}, action("practice_score", "27/30"), "Sam", TODAY)
        self.assertFalse(practice.SCORES.exists())

    def test_an_update_is_approved_by_the_updater(self):
        with mock.patch("butler.updates.approve", return_value="Updating to v0.2.0 tonight.") as approve:
            self.assertEqual(self.do("update", "yes, update"), "Updating to v0.2.0 tonight.")
        approve.assert_called_once_with()

    def test_a_question_is_answered_when_calendar_and_money_are_down(self):
        asked = []
        with mock.patch("butler.agenda.load_agenda", side_effect=RuntimeError("helper missing")), \
             mock.patch("butler.finance.build", side_effect=RuntimeError("Lunch Money down")), \
             mock.patch.object(replies.llm, "_ask_json",
                               side_effect=lambda s, system, prompt, schema: asked.append(prompt) or {"answer": "Friday."}):
            said = self.do("question", "When is the boiler service due?")
        self.assertEqual(said, "Answer: Friday.")
        facts = json.loads(asked[0].split("<<<facts\n", 1)[1].rsplit("\n>>>", 1)[0])
        self.assertEqual(set(facts), {"projects"})       # what could load, and nothing made up
        self.assertIn("Book the yearly boiler service and send the certificate to the landlord",
                      [p["do"] for p in facts["projects"]])


class ProcessingReplies(InAThrowawayHome):
    def reply(self, uid, body, sender="sam@example.com"):
        return inbox.Reply(uid=uid, message_id=f"<{uid}@example.test>", sender=sender, subject="Re: Bento",
                           body=body, trusted=True, why_untrusted="")

    def run_with(self, replies_in, actions, dry_run=False, send=None):
        self.sent, self.seen = getattr(self, "sent", []), []
        plan = actions if callable(actions) else (lambda s, r, t: actions[r.uid])
        with mock.patch.object(inbox, "fetch_unread", return_value=replies_in), \
             mock.patch.object(inbox, "mark_seen", side_effect=lambda s, uids: self.seen.extend(uids)), \
             mock.patch.object(replies, "plan_actions", side_effect=plan), \
             mock.patch.object(replies.mailer, "send_or_queue",
                               side_effect=send or (lambda *a, **k: self.sent.append((a, k)) or True)):
            out = io.StringIO()
            with redirect_stdout(out):
                handled = replies.process(SETTINGS, dry_run=dry_run)
        return handled, out.getvalue()

    def test_a_dry_run_shows_the_plan_and_does_nothing(self):
        handled, printed = self.run_with([self.reply("1", "add milk")],
                                         {"1": [action("add_shopping", "milk")]}, dry_run=True)
        self.assertEqual((handled, self.sent, self.seen, self.ek), (0, [], [], []))
        self.assertIn("From sam@example.com: Re: Bento", printed)
        self.assertIn("'type': 'add_shopping'", printed)
        self.assertNotIn("None", printed)               # empty fields aren't shown
        self.assertFalse(replies.PROCESSED.exists())

    def test_one_failing_action_does_not_stop_the_rest(self):
        actions = {"1": [action("step_note", "x", step_id="no-such-step"), action("add_shopping", "milk")]}
        with self.assertLogs("butler", "ERROR"):
            handled, _ = self.run_with([self.reply("1", "note it, and add milk")], actions)
        self.assertEqual(handled, 1)
        self.assertEqual(self.ek, [["add-reminder", "--list", "Shopping", "--title", "milk"]])
        (_, _, subject, body), kw = self.sent[0]
        self.assertRegex(body, r"- Couldn't do this \(.*no-such-step.*\): x\n")
        self.assertIn("- Added to Shopping: milk", body)
        self.assertEqual((subject, kw["in_reply_to"]), ("Re: Bento", "<1@example.test>"))
        self.assertEqual(kw["to"], ["sam@example.com", "alex@example.com"])
        self.assertEqual((self.seen, replies.PROCESSED.read_text().split()), (["1"], ["<1@example.test>"]))

    def test_a_reply_that_keeps_failing_costs_three_claude_calls_then_one_note(self):
        calls = []

        def plan(s, r, t):     # Claude answered (and was paid), then something broke
            calls.append(r.uid)
            raise ValueError("the plan couldn't be read")
        with self.assertLogs("butler", "INFO") as logs:
            for _ in range(6):
                self.run_with([self.reply("1", "add milk")], plan)
        self.assertEqual(len(calls), replies.MAX_ATTEMPTS)
        self.assertEqual(len(self.sent), 1, "the household hears once")
        (_, _, subject, body), kw = self.sent[0]
        self.assertIn("I couldn't handle Sam's reply after 3 tries", body)
        self.assertIn("Nothing in it was done", body)
        self.assertEqual((subject, kw["to"]), ("Re: Bento", ["sam@example.com", "alex@example.com"]))
        self.assertEqual(replies.PROCESSED.read_text().split(), ["<1@example.test>"])
        self.assertEqual(json.loads(replies.ATTEMPTS.read_text()), {}, "nothing left counting")
        self.assertIn("gave up on <1@example.test>", "\n".join(logs.output))

    def test_a_confirmation_that_fails_never_repeats_the_actions(self):
        def refuse(*a, **k):
            raise OSError("Gmail unreachable")
        with self.assertLogs("butler", "ERROR"):
            self.run_with([self.reply("1", "add milk")], {"1": [action("add_shopping", "milk")]}, send=refuse)
        handled, _ = self.run_with([self.reply("1", "add milk")], {"1": [action("add_shopping", "milk")]})
        self.assertEqual(handled, 0)
        self.assertEqual(self.ek, [["add-reminder", "--list", "Shopping", "--title", "milk"]], "milk added once")

    def test_one_failing_reply_does_not_stop_the_next(self):
        def plan(s, r, t):
            if r.uid == "1":
                raise ValueError("broken")
            return [action("add_shopping", "bread")]
        with self.assertLogs("butler", "ERROR"):
            handled, _ = self.run_with([self.reply("1", "x"), self.reply("2", "add bread", "alex@example.com")], plan)
        self.assertEqual(handled, 1)
        self.assertEqual(self.ek, [["add-reminder", "--list", "Shopping", "--title", "bread"]])
        self.assertEqual(json.loads(replies.ATTEMPTS.read_text()), {"<1@example.test>": 1})


if __name__ == "__main__":
    unittest.main()
