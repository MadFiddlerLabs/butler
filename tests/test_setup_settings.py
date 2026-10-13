"""The settings the `butler` command writes: the same shape `/butler-setup install` writes, with
nothing of the invented example household left in.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import os
import unittest
from unittest import mock

import yaml

from butler import setup_settings as ss
from fakes.account_api import FakeAccountAPI


class NameTest(unittest.TestCase):
    def test_valid_names(self):
        for name in ("Bento", "Mr Hudson", "Jean-Luc", "O'Brien", "R2", "José", "  Bento  "):
            self.assertTrue(ss.valid_name(name), name)
        self.assertEqual(ss.valid_name("  Mr   Hudson "), "Mr Hudson")

    def test_invalid_names(self):
        for name in ("", "   ", "a" * 25, "Bento!", "<script>", 'Bo"b', "--", "Bento\nX"):
            self.assertIsNone(ss.valid_name(name), name)


class FreshTest(unittest.TestCase):
    def test_no_example_household_left(self):
        s = ss.fresh()
        text = yaml.safe_dump(s)
        for invented in ("Johnson", "Sam", "Alex", "Mia", "sam@example.com", "Lisbon"):
            self.assertNotIn(invented, text, invented)
        self.assertEqual(s["name"], ss.PLACEHOLDER_NAME)
        self.assertEqual(s["household"]["adults"], [])
        self.assertNotIn("weather", s)
        self.assertNotIn("monthly_limit", s["llm"], "the Johnsons' limit isn't theirs")
        for key in ss.OPTIONAL_FEATURES:
            self.assertNotIn(key, s)
        self.assertEqual(s["portal"]["url"], "")

    def test_mac_timezone_falls_back(self):
        with mock.patch("os.readlink", side_effect=OSError):
            self.assertEqual(ss.mac_timezone(), "UTC")


class EditTest(unittest.TestCase):
    def test_name(self):
        s = ss.fresh()
        ss.set_name(s, "Bento")
        self.assertEqual((s["name"], s["email"]["sender_name"], s["reminders"]["todo_list"]),
                         ("Bento", "Bento", "Bento"))

    def test_adults_keep_emails_and_other_people(self):
        s = ss.fresh()
        ss.set_adults(s, ["Sam", "Alex"])
        s["household"]["adults"][0]["emails"] = ["sam@example.com"]
        s["people"].append({"name": "Mia", "aliases": ["Mia"], "calendars": [], "color": "#33b679"})
        ss.set_adults(s, ["Sam", "Alex", "Pat"])
        self.assertEqual(s["household"]["adults"][0]["emails"], ["sam@example.com"])
        self.assertEqual([p["name"] for p in s["people"]], ["Sam", "Alex", "Pat", "Mia"])
        self.assertTrue(all(p.get("color") for p in s["people"]))
        self.assertEqual(s["people"][3]["color"], "#33b679")

    def test_town_sets_timezone_weather_and_unit(self):
        s = ss.fresh()
        ss.set_town(s, ss.Place("Lisbon", "Portugal", "PT", 38.71667, -9.13333, "Europe/Lisbon"))
        self.assertEqual(s["timezone"], "Europe/Lisbon")
        self.assertEqual(s["household"]["place"], "Lisbon")
        self.assertEqual(s["weather"], {"place": "Lisbon", "latitude": 38.72, "longitude": -9.13, "unit": "celsius"})
        ss.set_town(s, ss.Place("Springfield", "United States", "US", 39.8, -89.64, "America/Chicago"))
        self.assertEqual(s["weather"]["unit"], "fahrenheit")


class GeocodeTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeAccountAPI().start()
        patcher = mock.patch.object(ss, "GEOCODE_URL", self.api.url + "/geocode")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.api.stop)

    def test_first_match(self):
        self.assertEqual(ss.find_town("Lisbon").country_code, "PT")

    def test_country_picks_among_matches(self):
        self.assertEqual(ss.find_town("Lisbon, United States").timezone, "America/New_York")
        self.assertEqual(ss.find_town("Lisbon, US").country, "United States")

    def test_unknown_town(self):
        self.assertIsNone(ss.find_town("Nowhereville"))


class RoundTripTest(unittest.TestCase):
    def test_saved_settings_pass_the_config_checks(self):
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "settings.yaml"
        with mock.patch.object(ss, "SETTINGS", path):
            s = ss.load()
            ss.set_name(s, "Bento")
            ss.set_adults(s, ["Sam", "Alex"])
            ss.set_town(s, ss.Place("Lisbon", "Portugal", "PT", 38.7, -9.1, "Europe/Lisbon"))
            ss.save(s)
            again = ss.load()
        self.assertEqual(again["name"], "Bento")
        self.assertTrue(path.read_text().startswith("# Written by the `butler` command."))
        for key in ("timezone", "email", "calendar", "reminders", "news", "llm", "household", "portal"):
            self.assertIn(key, again)
        os.remove(path)


if __name__ == "__main__":
    unittest.main()
