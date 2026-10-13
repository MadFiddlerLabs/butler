"""Guards the portal gate: unknown host names, missing logins through a proxy, logins not on
the list, browsers without a device key and cross-site posts are all refused.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import re
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from butler import devices, portal, render

SETTINGS = {"portal": {"url": "https://mac.tailnet.test", "hosts": ["butler.example.test"],
                       "allowed_logins": ["sam@example.com"]}}
TAILNET = "mac.tailnet.test"


def paired(test: unittest.TestCase) -> str:
    """A device store of the test's own, with one paired browser. Returns its key."""
    folder = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, folder, True)
    p = mock.patch.object(devices, "FILE", folder / "portal_devices.json")
    p.start()
    test.addCleanup(p.stop)
    return devices.pair(devices.new_code(), "Mac, Safari")


class GateTest(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.object(portal, "load_settings", return_value=SETTINGS),
                   mock.patch.object(portal, "_caddy", return_value=False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = portal.app.test_client(use_cookies=False)   # each request carries its own Cookie
        self.key = paired(self)

    def status(self, method="GET", **headers) -> int:
        """Through the gate with a paired browser's key: 404 means let in (no such page)."""
        headers.setdefault("Cookie", f"{devices.COOKIE}={self.key}")
        return self.client.open("/gate-test", method=method, headers=headers).status_code

    def test_this_mac_directly(self):
        self.assertNotEqual(self.status(Host="127.0.0.1:8787"), 403)
        self.assertNotEqual(self.status(Host="localhost:8787"), 403)

    def test_unknown_host_is_refused(self):
        self.assertEqual(self.status(Host="rebind.attacker.test"), 403)
        self.assertEqual(self.status(Host="rebind.attacker.test", **{"Tailscale-User-Login": "sam@example.com"}), 403)

    def test_tailnet_needs_a_login(self):
        self.assertEqual(self.status(Host=TAILNET), 403)
        self.assertEqual(self.status(Host="127.0.0.1:8787", **{"X-Forwarded-For": "100.64.0.9"}), 403)

    def test_allowed_and_unknown_logins(self):
        self.assertNotEqual(self.status(Host=TAILNET, **{"Tailscale-User-Login": "Sam@example.com"}), 403)
        self.assertNotEqual(self.status(Host="butler.example.test", **{"Tailscale-User-Login": "sam@example.com"}), 403)
        self.assertEqual(self.status(Host=TAILNET, **{"Tailscale-User-Login": "pat@example.com"}), 403)

    def test_caddy_peer_header_ignored_without_caddy(self):
        with mock.patch.object(portal, "_whois", return_value="sam@example.com"):
            self.assertEqual(self.status(Host=TAILNET, **{"X-Butler-Peer": "100.64.0.9"}), 403)

    def test_caddy_peer_header_with_caddy(self):
        with mock.patch.object(portal, "_caddy", return_value=True), \
             mock.patch.object(portal, "_whois", return_value="sam@example.com"):
            self.assertNotEqual(self.status(Host=TAILNET, **{"X-Butler-Peer": "100.64.0.9"}), 403)
            # Behind Caddy, the tailscale serve header means nothing.
            self.assertEqual(self.status(Host=TAILNET, **{"Tailscale-User-Login": "sam@example.com"}), 403)

    def test_this_mac_directly_still_needs_the_device_key(self):
        self.assertEqual(self.status(Host="127.0.0.1:8787"), 404)
        self.assertEqual(self.status(Host="127.0.0.1:8787", Cookie=""), 303)
        self.assertEqual(self.status(Host="127.0.0.1:8787", Cookie=f"{devices.COOKIE}=forged"), 303)

    def test_post_needs_the_same_origin(self):
        login = {"Tailscale-User-Login": "sam@example.com"}
        self.assertNotEqual(self.status("POST", Host=TAILNET, Origin=f"https://{TAILNET}", **login), 403)
        self.assertEqual(self.status("POST", Host=TAILNET, Origin=f"https://{TAILNET}.attacker.test", **login), 403)
        self.assertEqual(self.status("POST", Host=TAILNET, Origin="https://attacker.test", **login), 403)
        self.assertEqual(self.status("POST", Host=TAILNET, **login), 403)



class DeviceKeyTest(unittest.TestCase):
    """Any account or process on the Mac reaches 127.0.0.1 and can forge the login headers there.
    Only a paired browser's device key gets in."""

    LOCAL = {"Host": "127.0.0.1:8787"}
    TAILNET_LOGIN = {"Host": TAILNET, "Tailscale-User-Login": "sam@example.com"}

    def setUp(self):
        for p in (mock.patch.object(portal, "load_settings", return_value=SETTINGS),
                  mock.patch.object(portal, "_caddy", return_value=False)):
            p.start()
            self.addCleanup(p.stop)
        self.client = portal.app.test_client(use_cookies=False)   # each request carries its own Cookie
        self.key = paired(self)

    def get(self, path, key=None, **headers):
        if key:
            headers["Cookie"] = f"{devices.COOKIE}={key}"
        return self.client.get(path, headers=headers)

    def pair(self, code, **headers):
        return self.client.post("/pair", data={"code": code},
                                headers={"Origin": f"http://{headers.get('Host', '127.0.0.1:8787')}", **headers})

    def test_forged_login_headers_without_a_key_get_nothing(self):
        for headers in (self.LOCAL, self.TAILNET_LOGIN, {**self.LOCAL, "Tailscale-User-Login": "sam@example.com"}):
            r = self.get("/info", **headers)
            self.assertEqual((r.status_code, r.headers["Location"]), (303, "/pair"), headers)
            post = self.client.post("/lists/add", data={"list": "Shopping", "title": "x"},
                                    headers={**headers, "Origin": f"http://{headers['Host']}"})
            self.assertEqual(post.status_code, 403, headers)
        with mock.patch.object(portal, "_caddy", return_value=True), \
             mock.patch.object(portal, "_whois", return_value="sam@example.com"):
            self.assertEqual(self.get("/info", Host=TAILNET, **{"X-Butler-Peer": "100.64.0.9"}).status_code, 303)

    def test_a_code_pairs_one_browser_once(self):
        unpaired = self.get("/pair", **self.LOCAL)
        self.assertEqual(unpaired.status_code, 200)
        self.assertNotIn('href="/info"', unpaired.get_data(as_text=True))     # no way in shown, no names
        code = devices.new_code()
        r = self.pair(code.lower().replace("-", " "), **self.LOCAL)      # typed loosely
        self.assertEqual((r.status_code, r.headers["Location"]), (303, "/"))
        cookie = r.headers["Set-Cookie"]
        key = re.search(rf"{devices.COOKIE}=([^;]+)", cookie).group(1)
        for flag in ("HttpOnly", "SameSite=Lax", "Path=/", f"Max-Age={portal.KEY_DAYS * 86400}"):
            self.assertIn(flag, cookie)
        self.assertNotIn("Secure", cookie)                                # plain http on the Mac itself
        self.assertEqual(self.get("/gate-test", key, **self.LOCAL).status_code, 404)
        self.assertEqual(self.pair(code, **self.LOCAL).status_code, 403)  # used up
        self.assertEqual(self.get("/pair", key, **self.LOCAL).status_code, 303)

    def test_over_the_tailnet_the_key_is_https_only_and_renewed(self):
        r = self.pair(devices.new_code(), **self.TAILNET_LOGIN)
        self.assertIn("Secure", r.headers["Set-Cookie"])
        key = re.search(rf"{devices.COOKIE}=([^;]+)", r.headers["Set-Cookie"]).group(1)
        again = self.get("/gate-test", key, **self.TAILNET_LOGIN)
        self.assertIn(f"{devices.COOKIE}={key}", again.headers["Set-Cookie"])
        # The key doesn't replace the login: a stranger's login with it is still refused.
        self.assertEqual(self.get("/gate-test", key, Host=TAILNET, **{"Tailscale-User-Login": "pat@example.com"})
                         .status_code, 403)

    def test_expired_codes_and_guessing_fail(self):
        old = devices.new_code(now=datetime.now().astimezone() - timedelta(minutes=devices.CODE_MINUTES + 1))
        self.assertEqual(self.pair(old, **self.LOCAL).status_code, 403)
        fresh = devices.new_code()
        for _ in range(devices.MAX_MISSES - 1):
            self.assertEqual(self.pair("AAAA-AAAA", **self.LOCAL).status_code, 403)
        self.assertEqual(self.pair("AAAA-AAAA", **self.LOCAL).status_code, 403)   # the fifth miss
        self.assertEqual(self.pair(fresh, **self.LOCAL).status_code, 403)         # cancelled with it

    def test_only_the_macs_own_file_may_post_a_code_without_an_origin(self):
        code = devices.new_code()
        self.assertEqual(self.client.post("/pair", data={"code": code},
                                          headers={**self.TAILNET_LOGIN, "Origin": "null"}).status_code, 403)
        self.assertEqual(self.client.post("/lists/add", data={"list": "Shopping", "title": "x"},
                                          headers={**self.LOCAL, "Origin": "null",
                                                   "Cookie": f"{devices.COOKIE}={self.key}"}).status_code, 403)
        r = self.client.post("/pair", data={"code": code}, headers={**self.LOCAL, "Origin": "null"})
        self.assertEqual(r.status_code, 303)

    def test_the_devices_page_adds_and_forgets(self):
        with_key = {**self.LOCAL, "Cookie": f"{devices.COOKIE}={self.key}", "Origin": "http://127.0.0.1:8787"}
        page = self.client.get("/devices", headers=with_key)
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        self.assertIn("this one", page.get_data(as_text=True))
        shown = self.client.post("/devices/code", headers=with_key).get_data(as_text=True)
        code = re.search(r"<code>([A-Z0-9]{4}-[A-Z0-9]{4})</code>", shown).group(1)
        self.assertIn("https://mac.tailnet.test", shown)
        phone = self.pair(code, **self.TAILNET_LOGIN, **{"User-Agent": "Mozilla/5.0 (iPhone) Safari/604.1"})
        self.assertEqual(phone.status_code, 303)
        listed = devices.listing()
        self.assertEqual([d["label"] for d in listed], ["Mac, Safari", "iPhone, Safari"])
        self.client.post(f"/devices/{listed[1]['id']}/forget", headers=with_key)
        phone_key = re.search(rf"{devices.COOKIE}=([^;]+)", phone.headers["Set-Cookie"]).group(1)
        self.assertEqual(self.get("/gate-test", phone_key, **self.TAILNET_LOGIN).status_code, 303)

    def test_the_store_is_owner_only_and_holds_no_key_or_code(self):
        code = devices.new_code()
        stored = devices.FILE.read_text()
        self.assertEqual(devices.FILE.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.key, stored)
        self.assertNotIn(code.replace("-", ""), stored)

    def test_the_icon_and_manifest_are_open_but_still_host_checked(self):
        self.assertEqual(self.get("/manifest.json", **self.LOCAL).status_code, 200)
        icon = self.get("/static/icon-180.png", **self.LOCAL)
        icon.close()
        self.assertEqual(icon.status_code, 200)
        self.assertEqual(self.get("/manifest.json", Host="rebind.attacker.test").status_code, 403)


def tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9]+):(#[0-9a-f]{6})", block))


class AccessibleTest(unittest.TestCase):
    """Accessibility: text contrast, the current page and named controls."""
    LIGHT = tokens(portal.CSS.split("@media")[0])
    DARK = {**LIGHT, **tokens(portal.CSS.split("@media")[1].split("}}")[0])}

    def test_text_tokens_reach_4_5_on_page_and_card(self):
        for mode, t in (("light", self.LIGHT), ("dark", self.DARK)):
            for text in ("ink", "ink2", "muted", "blue", "accent"):
                for surface in ("bg", "card"):
                    ratio = render.contrast(t[text], t[surface])
                    self.assertGreaterEqual(ratio, 4.5, f"{mode} --{text} on --{surface}: {ratio:.2f}")
            self.assertGreaterEqual(render.contrast("#ffffff", t["chipblue"]), 4.5, f"{mode} 'now' chip")
            self.assertGreaterEqual(render.contrast(t["ink2"], t["chip"]), 4.5, f"{mode} chip")

    def test_the_current_page_is_marked_beyond_color(self):
        with mock.patch.object(portal, "load_settings", return_value=SETTINGS):
            html = portal.page("Lists", "", "/lists")
        self.assertIn('<a href="/lists" aria-current="page">Lists</a>', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn("nav a[aria-current=page]{color:var(--accent);text-decoration:underline", portal.CSS)

    def test_list_controls_have_names(self):
        settings = {**SETTINGS, "reminders": {"todo_list": "Johnsons", "shopping_list": "Shopping"}}
        items = [{"id": "r1", "title": "Call the plumber (invented)"}]
        with mock.patch.object(portal, "load_settings", return_value=settings), \
                mock.patch.object(portal, "_ek", return_value=items):
            html = portal.lists()
        self.assertIn('<button aria-label="Done: Call the plumber (invented)">✓</button>', html)
        self.assertIn('<label class=vh for="add-todo_list">Add to Johnsons</label>'
                      '<input type=text id="add-todo_list"', html)
        self.assertIn('<label class=vh for="add-shopping_list">Add to Shopping</label>', html)

    def test_step_controls_have_names(self):
        step = SimpleNamespace(id="visa-3", do="Book the visa appointment", status="todo", every=None)
        self.assertIn('<button aria-label="Done: Book the visa appointment">Done</button>', portal._buttons(step))
        self.assertIn('<button aria-label="Pause: Book the visa appointment">Pause</button>', portal._buttons(step))
        form = portal._update_form(step)
        self.assertIn('<label class=vh for="upd-visa-3">Send an update on Book the visa appointment</label>'
                      '<textarea id="upd-visa-3"', form)

    def test_a_person_is_drawn_as_the_emails_draw_them(self):
        settings = {**SETTINGS, "people": [{"name": "Alex", "color": "#e67c73", "color_dark": "#f4a199"}]}
        with mock.patch.object(portal, "load_settings", return_value=settings):
            css = portal._people_css()
        darker = render.readable("#e67c73")
        self.assertIn(f"--p0:{darker}", css)
        self.assertIn(f'.brief [style*="color:{darker}"]', css)
        self.assertIn("--p0:#f4a199", css)


if __name__ == "__main__":
    unittest.main()
