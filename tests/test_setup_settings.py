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

    def test_none_of_the_johnsons_choices_either(self):
        """Found by the persona tests: a household in Leeds was told its currency is the euro."""
        s = ss.fresh()
        text = yaml.safe_dump(s)
        for theirs in ("Example Bank", "Little sport", "schools and transport", "NPR"):
            self.assertNotIn(theirs, text, theirs)
        self.assertNotIn("second", s["currency"], "the Johnsons are paid in USD; a new household isn't")
        self.assertEqual([r["key"] for r in s["news"]["regions"]], ["world"])
        self.assertEqual(s["calendar"]["source"], "google" if ss.system.NAME == "windows" else "apple")


class TownCurrencyAndNewsTest(unittest.TestCase):
    LEEDS = ss.Place("Leeds", "United Kingdom", "GB", 53.8, -1.55, "Europe/London")
    PARIS_FR = ss.Place("Paris", "France", "FR", 48.85, 2.35, "Europe/Paris")
    PARIS_TX = ss.Place("Paris", "United States", "US", 33.66, -95.56, "America/Chicago", "Texas")
    TOKYO = ss.Place("Tokyo", "Japan", "JP", 35.69, 139.69, "Asia/Tokyo")

    def keys(self, s):
        return [r["key"] for r in s["news"]["regions"]]

    def test_the_first_town_brings_its_currency_and_news(self):
        s = ss.fresh()
        ss.set_town(s, self.LEEDS)
        self.assertEqual((s["currency"]["primary"], self.keys(s)), ("GBP", ["europe", "world"]))
        s = ss.fresh()
        ss.set_town(s, self.TOKYO)
        self.assertEqual((s["currency"]["primary"], self.keys(s)), ("JPY", ["world"]))

    def test_a_corrected_town_corrects_them(self):
        s = ss.fresh()
        ss.set_town(s, self.PARIS_FR)
        self.assertEqual((s["currency"]["primary"], self.keys(s)), ("EUR", ["europe", "world"]))
        ss.set_town(s, self.PARIS_TX)
        self.assertEqual((s["currency"]["primary"], self.keys(s)), ("USD", ["us", "world"]))

    def test_the_households_own_choices_stay(self):
        s = ss.fresh()
        ss.set_town(s, self.LEEDS)
        s["currency"]["primary"] = "EUR"                       # Lunch Money's, in Module 7
        s["news"]["regions"].insert(0, {"key": "local", "label": "Leeds", "feeds": []})   # Module 2
        ss.set_town(s, self.PARIS_TX)
        self.assertEqual((s["currency"]["primary"], self.keys(s)), ("EUR", ["local", "europe", "world"]))

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
        self.assertEqual(s["weather"], {"place": "Lisbon", "latitude": 38.72, "longitude": -9.13, "unit": "celsius",
                                        "country": "PT"})
        ss.set_town(s, ss.Place("Springfield", "United States", "US", 39.8, -89.64, "America/Chicago"))
        self.assertEqual(s["weather"]["unit"], "fahrenheit")

    def test_pasting_the_town_again_keeps_the_rest_of_the_weather_block(self):
        """A household that chose Fahrenheit in Lisbon keeps it when it pastes the town again."""
        s = ss.fresh()
        ss.set_town(s, ss.Place("Lisbon", "Portugal", "PT", 38.71667, -9.13333, "Europe/Lisbon"))
        s["weather"]["unit"] = "fahrenheit"
        s["weather"]["note"] = "kept"
        ss.set_town(s, ss.Place("Lisbon", "Portugal", "PT", 38.7, -9.1, "Europe/Lisbon"))
        self.assertEqual((s["weather"]["unit"], s["weather"]["note"]), ("fahrenheit", "kept"))
        # Another country: that country's unit. Paris, France, then the right Paris, in Texas.
        ss.set_town(s, ss.Place("Paris", "France", "FR", 48.85, 2.35, "Europe/Paris"))
        self.assertEqual(s["weather"]["unit"], "celsius")
        ss.set_town(s, ss.Place("Paris", "United States", "US", 33.66, -95.56, "America/Chicago", "Texas"))
        self.assertEqual((s["weather"]["unit"], s["weather"]["country"], s["timezone"]),
                         ("fahrenheit", "US", "America/Chicago"))

    def test_an_older_weather_block_without_a_country_keeps_its_unit(self):
        s = ss.fresh()
        s["weather"] = {"place": "Lisbon", "latitude": 38.72, "longitude": -9.14, "unit": "fahrenheit"}
        ss.set_town(s, ss.Place("Lisbon", "Portugal", "PT", 38.7, -9.1, "Europe/Lisbon"))
        self.assertEqual(s["weather"]["unit"], "fahrenheit")


class ClaudeModeTest(unittest.TestCase):
    def test_unset_until_chosen_and_only_two_values(self):
        self.assertIsNone(ss.claude_mode(ss.fresh()), "a new household is asked once")
        self.assertEqual(ss.claude_mode({"claude_mode": "auto"}), "auto")
        self.assertEqual(ss.claude_mode({"claude_mode": " Ask "}), "ask")
        self.assertIsNone(ss.claude_mode({"claude_mode": "bypassPermissions"}))
        self.assertEqual(ss.PERMISSION_MODES, {"ask": "default", "auto": "auto"})

    def test_the_example_says_ask_first(self):
        example = yaml.safe_load(ss.EXAMPLE.read_text())
        self.assertEqual(example["claude_mode"], "ask")


class DoneStepsTest(unittest.TestCase):
    def test_what_the_first_run_can_skip(self):
        import tempfile
        from pathlib import Path
        root = Path(tempfile.mkdtemp())
        self.assertEqual(ss.done_steps(root), [])
        (root / "config").mkdir()
        s = ss.fresh()
        (root / "config" / "settings.yaml").write_text(yaml.safe_dump(s))
        self.assertEqual(ss.done_steps(root), [])
        ss.set_adults(s, ["Sam"])
        ss.set_town(s, ss.Place("Lisbon", "Portugal", "PT", 38.7, -9.1, "Europe/Lisbon"))
        (root / "config" / "settings.yaml").write_text(yaml.safe_dump(s))
        self.assertEqual(ss.done_steps(root), ["town", "adults"])
        (root / "out" / "preview").mkdir(parents=True)
        (root / "out" / "preview" / "brief-2026-10-12.html").write_text("<p>")
        self.assertEqual(ss.done_steps(root), ["town", "adults", "brief"])
        (root / "config" / "settings.yaml").write_text("{ not yaml")
        self.assertEqual(ss.done_steps(root), ["brief"])


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

    def test_state_or_region_picks_among_matches(self):
        """The lesson's own examples (Module 0): without the state, the biggest town of the name wins."""
        illinois = ss.find_town("Springfield, Illinois")
        self.assertEqual((illinois.region, illinois.latitude, illinois.timezone), ("Illinois", 39.80172, "America/Chicago"))
        self.assertEqual(illinois.label(), "Springfield, Illinois, United States")
        texas = ss.find_town("Paris, Texas")
        self.assertEqual((texas.region, texas.country_code, texas.timezone), ("Texas", "US", "America/Chicago"))
        self.assertEqual(ss.find_town("Paris, TX").region, "Texas")
        self.assertEqual(ss.find_town("paris, texas, united states").region, "Texas")
        self.assertEqual(ss.find_town("Springfield, MA").timezone, "America/New_York")

    def test_without_a_state_the_biggest_wins_and_the_others_are_named(self):
        paris = ss.find_town("Paris")
        self.assertEqual((paris.country, paris.timezone), ("France", "Europe/Paris"))
        self.assertEqual(paris.others, ["Paris, Texas", "Paris, Tennessee"])
        springfield = ss.find_town("Springfield")
        self.assertEqual(springfield.region, "Missouri")
        self.assertNotIn("Palmyra, Missouri", springfield.others, "only towns of that name")
        self.assertEqual(ss.find_town("Paris, Île-de-France").country_code, "FR")

    def test_an_unknown_region_falls_back_to_the_biggest(self):
        self.assertEqual(ss.find_town("Paris, Narnia").country_code, "FR")


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
