"""The brief's news before Claude is connected (daily._raw_headlines): each region's first
stories as the feeds give them, and a story two regions' feeds share shows once.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""
import unittest

from butler import daily
from butler.news import Story

SETTINGS = {"news": {"stories_per_region": 2, "regions": [{"key": "europe"}, {"key": "world"}]}}


def story(region, n, title, link):
    return Story(f"{region}-{n}", region, "Example News", title, "Invented.", link, None)


class RawHeadlinesTest(unittest.TestCase):
    def test_a_story_in_two_regions_shows_once_and_the_next_takes_its_place(self):
        stories = [story("europe", 1, "Lisbon trams back", "https://news.example.test/trams"),
                   story("europe", 2, "Porto bridge opens", "https://news.example.test/bridge"),
                   story("world", 1, "Lisbon trams back", "https://news.example.test/trams"),
                   story("world", 2, "Ocean survey ends", "https://news.example.test/ocean"),
                   story("world", 3, "Rain in  Lisbon", ""),
                   story("world", 4, "rain in lisbon", "")]
        got = [(h["story"].region, h["headline"]) for h in daily._raw_headlines(SETTINGS, stories)]
        self.assertEqual(got, [("europe", "Lisbon trams back"), ("europe", "Porto bridge opens"),
                               ("world", "Ocean survey ends"), ("world", "Rain in  Lisbon")])


if __name__ == "__main__":
    unittest.main()
