"""Guards the portal gate: unknown host names, missing logins through a proxy, logins not on
the list and cross-site posts are all refused.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import unittest
from unittest import mock

from casa import portal

SETTINGS = {"portal": {"url": "https://mac.tailnet.test", "hosts": ["casa.example.test"],
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
        self.assertNotEqual(self.status(Host="casa.example.test", **{"Tailscale-User-Login": "sam@example.com"}), 403)
        self.assertEqual(self.status(Host=TAILNET, **{"Tailscale-User-Login": "pat@example.com"}), 403)

    def test_caddy_peer_header_ignored_without_caddy(self):
        with mock.patch.object(portal, "_whois", return_value="sam@example.com"):
            self.assertEqual(self.status(Host=TAILNET, **{"X-Casa-Peer": "100.64.0.9"}), 403)

    def test_caddy_peer_header_with_caddy(self):
        with mock.patch.object(portal, "_caddy", return_value=True), \
             mock.patch.object(portal, "_whois", return_value="sam@example.com"):
            self.assertNotEqual(self.status(Host=TAILNET, **{"X-Casa-Peer": "100.64.0.9"}), 403)
            # Behind Caddy, the tailscale serve header means nothing.
            self.assertEqual(self.status(Host=TAILNET, **{"Tailscale-User-Login": "sam@example.com"}), 403)

    def test_post_needs_the_same_origin(self):
        login = {"Tailscale-User-Login": "sam@example.com"}
        self.assertNotEqual(self.status("POST", Host=TAILNET, Origin=f"https://{TAILNET}", **login), 403)
        self.assertEqual(self.status("POST", Host=TAILNET, Origin=f"https://{TAILNET}.attacker.test", **login), 403)
        self.assertEqual(self.status("POST", Host=TAILNET, Origin="https://attacker.test", **login), 403)
        self.assertEqual(self.status("POST", Host=TAILNET, **login), 403)


if __name__ == "__main__":
    unittest.main()
