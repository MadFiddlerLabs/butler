"""The account API contract (docs/ACCOUNTS.md) against the fake that every other test signs in
with (tests/fakes/account_api.py). The doc's table is parsed, each documented call is made
through the fake with the documented body, and the answer must have exactly the documented
shape. Every call the setup app makes (account.py and first-run.sh) must be in the doc. So
the fake can't drift from the contract the website implements, and the engine can't call
something the contract doesn't have.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import json
import re
import unittest
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from butler import progress
from fakes.account_api import FakeAccountAPI

REPO = Path(__file__).resolve().parents[1]
DOC = (REPO / "docs" / "ACCOUNTS.md").read_text()


@dataclass
class Shape:
    """A documented JSON object: {key: allowed values (None = any)}, and which keys may be absent."""
    keys: dict
    optional: set


def parse_shape(text: str) -> Shape:
    """`{a, b:"x"|"y", c?, d:[{e, f}]}` -> Shape. Values: quoted strings and true/false are
    enums; a list of objects is parsed too; anything else means 'any value'."""
    text = text.strip()
    assert text.startswith("{") and text.endswith("}"), text
    body, keys, optional, depth, part = text[1:-1], {}, set(), 0, ""
    parts = []
    for ch in body:
        depth += ch in "[{" and 1 or (ch in "]}" and -1 or 0)
        if ch == "," and depth == 0:
            parts.append(part)
            part = ""
        else:
            part += ch
    parts.append(part)
    for p in filter(str.strip, parts):
        name, _, value = (x.strip() for x in p.partition(":"))
        if name.endswith("?"):
            name = name[:-1]
            optional.add(name)
        if value.startswith("[{"):
            keys[name] = [parse_shape(value[1:-1])]
        elif value:
            keys[name] = {json.loads(v.strip()) for v in value.split("|")}
        else:
            keys[name] = None
    return Shape(keys, optional)


@dataclass
class Call:
    method: str
    path: str
    bearer: bool
    body: Shape | None
    answer: Shape


def documented() -> dict[str, Call]:
    section = DOC.split("## The account API")[1].split("\n## ")[0]
    rows = [l for l in section.splitlines() if l.startswith("|")][2:]     # past the header and rule
    calls = {}
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        # Enum bars inside a cell split it too: glue the cells back by their backticks.
        joined = " | ".join(cells)
        endpoint = re.match(r"`(GET|POST) (/[^`]+)`", joined)
        if not endpoint or not endpoint.group(2).startswith("/api/"):
            continue          # GET /verify is a web page, not part of the API
        rest = joined[endpoint.end():]
        shapes = re.findall(r"`(\{[^`]*\})`", rest)
        bearer = "Bearer" in rest.split("`{")[0] or rest.strip(" |").startswith("header `Authorization")
        body = None
        if re.match(r"\s*\|\s*(Bearer \+ )?`\{", rest):
            body, answer = parse_shape(shapes[0]), parse_shape(shapes[1])
        else:
            answer = parse_shape(shapes[0])
        calls[f"{endpoint.group(1)} {endpoint.group(2)}"] = Call(endpoint.group(1), endpoint.group(2), bearer, body, answer)
    return calls


CALLS = documented()


class DocParseTest(unittest.TestCase):
    """The parser reads the doc as written; if the table's form changes, this says so first."""

    def test_every_call_is_read(self):
        self.assertEqual(set(CALLS), {"POST /api/auth/start", "POST /api/auth/verify", "POST /api/auth/poll",
                                      "GET /api/me", "POST /api/progress", "POST /api/prefs",
                                      "POST /api/auth/logout", "POST /api/account/delete"})
        self.assertEqual(CALLS["POST /api/auth/poll"].answer.keys["status"], {"pending", "verified", "expired"})
        self.assertEqual(CALLS["POST /api/progress"].body.optional, {"at"})
        self.assertIsInstance(CALLS["GET /api/me"].answer.keys["progress"], list)
        self.assertTrue(CALLS["POST /api/prefs"].bearer)
        self.assertTrue(CALLS["GET /api/me"].bearer)
        self.assertFalse(CALLS["POST /api/auth/start"].bearer)
        self.assertEqual(CALLS["POST /api/auth/start"].body.optional, {"heard"})


class EngineCallsAreDocumentedTest(unittest.TestCase):
    def test_account_py(self):
        source = (REPO / "src" / "butler" / "account.py").read_text()
        used = set(re.findall(r'_call\("(GET|POST)", "(/[^"]+)"', source))
        self.assertTrue(used)
        for method, path in used:
            self.assertIn(f"{method} /api{path}", CALLS)

    def test_first_run(self):
        script = (REPO / "scripts" / "first-run.sh").read_text()
        used = set(re.findall(r"\bpost (/[a-z/]+)", script))
        self.assertTrue(used)
        for path in used:
            self.assertIn(f"POST /api{path}", CALLS)

    def test_the_bodies_the_engine_sends_have_the_documented_keys(self):
        script = (REPO / "scripts" / "first-run.sh").read_text()
        start = re.search(r'post /auth/start "(\{.*\})"', script).group(1)
        sent = set(re.findall(r'\\"(\w+)\\":', start))
        self.assertEqual(sent, set(CALLS["POST /api/auth/start"].body.keys))
        source = (REPO / "src" / "butler" / "account.py").read_text()
        for key in CALLS["POST /api/auth/start"].body.keys:
            if not key.startswith("terms_"):   # account.terms() adds those two
                self.assertIn(f'"{key}"', source.split("def start(")[1].split("\ndef ")[0])


class FakeFollowsTheDocTest(unittest.TestCase):
    """Each documented call through the fake, with the documented body: the answer has exactly the
    documented keys and only documented values."""

    def setUp(self):
        self.api = FakeAccountAPI(known_lessons=set(progress.course_map()["calendar"]["lessons"]) | {"setup.email"})
        self.api.start()
        self.addCleanup(self.api.stop)

    def call(self, key: str, body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
        doc = CALLS[key]
        if doc.body is not None and body is not None:
            self.assertLessEqual(set(body), set(doc.body.keys), f"{key}: the test sends undocumented keys")
            self.assertLessEqual(set(doc.body.keys) - doc.body.optional, set(body), f"{key}: missing required keys")
        req = urllib.request.Request(self.api.url + doc.path, method=doc.method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def assert_shape(self, answer: dict, shape: Shape, where: str):
        self.assertLessEqual(set(answer), set(shape.keys), f"{where}: undocumented keys")
        self.assertLessEqual(set(shape.keys) - shape.optional, set(answer), f"{where}: documented keys missing")
        for name, allowed in shape.keys.items():
            if name not in answer:
                continue
            if isinstance(allowed, set):
                self.assertIn(answer[name], allowed, f"{where}.{name}")
            elif isinstance(allowed, list):
                self.assertIsInstance(answer[name], list, f"{where}.{name}")
                for item in answer[name]:
                    self.assert_shape(item, allowed[0], f"{where}.{name}[]")

    def sign_in(self, client="setup/test") -> str:
        start = CALLS["POST /api/auth/start"]
        code, answer = self.call("POST /api/auth/start", {
            "email": "sam@example.com", "butler_name": "Bento", "client": client,
            "terms_version": "1.0-beta", "terms_accepted_at": "2026-10-10T18:00:00Z", "heard": "friend"})
        self.assertEqual(code, 200)
        self.assert_shape(answer, start.answer, "auth/start")
        code, polled = self.call("POST /api/auth/poll", {"request_id": answer["request_id"],
                                                         "poll_secret": answer["poll_secret"]})
        self.assertEqual((code, polled), (200, {"status": "pending"}))
        code, verified = self.call("POST /api/auth/verify", {"token": self.api.links[-1]})
        self.assertEqual(code, 200)
        self.assert_shape(verified, CALLS["POST /api/auth/verify"].answer, "auth/verify")
        self.assertEqual(verified["client"], client.split("/")[0])
        code, polled = self.call("POST /api/auth/poll", {"request_id": answer["request_id"],
                                                         "poll_secret": answer["poll_secret"]})
        self.assertEqual(polled["status"], "verified")
        self.assertIn("session_token", polled, "the session comes with the first verified poll")
        token = polled.pop("session_token")
        self.assert_shape(polled, CALLS["POST /api/auth/poll"].answer, "auth/poll")
        _, again = self.call("POST /api/auth/poll", {"request_id": answer["request_id"],
                                                     "poll_secret": answer["poll_secret"]})
        self.assertNotIn("session_token", again, "returned once")
        return token

    def test_the_whole_contract(self):
        token = self.sign_in()
        lesson = progress.course_map()["calendar"]["lessons"][0]

        for status in ("started", "done"):
            code, answer = self.call("POST /api/progress", {"lesson": lesson, "status": status,
                                                            "at": "2026-10-11T09:00:00Z"}, token)
            self.assertEqual((code, answer), (200, {"ok": True}))
        code, answer = self.call("POST /api/progress", {"lesson": "setup.email", "status": "done"}, token)
        self.assertEqual((code, answer), (200, {"ok": True}), "`at` is optional")
        code, answer = self.call("POST /api/prefs", {"nudges": True}, token)
        self.assertEqual((code, answer), (200, {"ok": True}))

        code, me = self.call("GET /api/me", None, token)
        self.assertEqual(code, 200)
        self.assert_shape(me, CALLS["GET /api/me"].answer, "me")
        self.assertEqual((me["email"], me["butler_name"], me["nudges"]), ("sam@example.com", "Bento", True))
        self.assertEqual({(p["lesson"], p["status"]) for p in me["progress"]},
                         {(lesson, "done"), ("setup.email", "done")}, "a done lesson stays done; idempotent")

        code, answer = self.call("POST /api/auth/logout", None, token)
        self.assertEqual((code, answer), (200, {"ok": True}))
        self.assertEqual(self.call("GET /api/me", None, token)[0], 401, "logout revokes the session")

    def test_refusals(self):
        token = self.sign_in()
        code, answer = self.call("POST /api/progress", {"lesson": "m99-not-a-lesson", "status": "done"}, token)
        self.assertEqual((code, answer.get("code")), (400, "bad_lesson"))
        self.assertIn("error", answer)
        code, answer = self.call("POST /api/progress", {"lesson": "setup.email", "status": "undone"}, token)
        self.assertEqual(code, 400, "the setup app's statuses are started and done")
        self.assertEqual(self.call("POST /api/prefs", {"nudges": "yes"}, token)[0], 400)
        self.assertEqual(self.call("GET /api/me", None, "not-a-session")[0], 401)
        code, answer = self.call("POST /api/auth/start", {
            "email": "sam@example.com", "butler_name": "Bento", "client": "setup/test", "terms_version": "1.0-beta",
            "terms_accepted_at": "2026-10-10T18:00:00Z", "heard": "x" * 41})
        self.assertEqual((code, answer.get("code")), (400, "bad_heard"), "never more than a short answer")
        self.assertEqual(self.call("POST /api/progress", {"lesson": "setup.email", "status": "done"})[0], 401)
        for key in ("POST /api/auth/poll",):
            code, answer = self.call(key, {"request_id": "nope", "poll_secret": "nope"})
            self.assertGreaterEqual(code, 400)
            self.assertIsInstance(answer.get("error"), str, "errors are readable strings")

    def test_delete_takes_everything(self):
        token = self.sign_in(client="web/1")
        self.assertEqual(self.call("POST /api/account/delete", {"confirm": False}, token)[0], 400)
        code, answer = self.call("POST /api/account/delete", {"confirm": True}, token)
        self.assertEqual((code, answer), (200, {"ok": True}))
        self.assertEqual(self.call("GET /api/me", None, token)[0], 401)


if __name__ == "__main__":
    unittest.main()
