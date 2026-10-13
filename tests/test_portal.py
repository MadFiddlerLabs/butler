"""Guards the portal gate: unknown host names, missing logins through a proxy, logins not on
the list and cross-site posts are all refused.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import re
import unittest
from types import SimpleNamespace
from unittest import mock

from butler import portal, render

SETTINGS = {"portal": {"url": "https://mac.tailnet.test", "hosts": ["butler.example.test"],
                       "allowed_logins": ["sam@example.com"]}}
TAILNET = "mac.tailnet.test"


class GateTest(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.object(portal, "load_settings", return_value=SETTINGS),
                   mock.patch.object(portal, "_caddy", return_value=False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = portal.app.test_client()

    def status(self, method="GET", **headers) -> int:
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

    def test_post_needs_the_same_origin(self):
        login = {"Tailscale-User-Login": "sam@example.com"}
        self.assertNotEqual(self.status("POST", Host=TAILNET, Origin=f"https://{TAILNET}", **login), 403)
        self.assertEqual(self.status("POST", Host=TAILNET, Origin=f"https://{TAILNET}.attacker.test", **login), 403)
        self.assertEqual(self.status("POST", Host=TAILNET, Origin="https://attacker.test", **login), 403)
        self.assertEqual(self.status("POST", Host=TAILNET, **login), 403)



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
