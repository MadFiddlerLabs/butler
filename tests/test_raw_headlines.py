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


class EmptyRegionsTest(unittest.TestCase):
    """A region Claude picked nothing for falls back to its feed's own headlines (daily._fill_empty_regions)."""

    def setUp(self):
        from unittest import mock
        self.sent = []
        p = mock.patch.object(daily.llm, "recent_news", side_effect=lambda day: self.sent)
        p.start()
        self.addCleanup(p.stop)
        self.stories = [story("europe", 1, "Lisbon trams back", "https://news.example.test/trams"),
                        story("europe", 2, "Porto bridge opens", "https://news.example.test/bridge"),
                        story("world", 1, "Lisbon trams back", "https://news.example.test/trams"),
                        story("world", 2, "Ocean survey ends", "https://news.example.test/ocean"),
                        story("world", 3, "Moon mission waits", "https://news.example.test/moon")]

    def pick(self, s, headline):
        return {"story": s, "headline": headline, "summary": "Edited."}

    def test_a_region_claude_left_empty_shows_its_feed_after_the_edited_ones(self):
        news = [self.pick(self.stories[1], "Porto's new bridge opens")]
        got = [(h["story"].region, h["headline"]) for h in daily._fill_empty_regions(SETTINGS, self.stories, news, None)]
        # The trams story is World's first, but Europe's feed carried it too: World keeps it, as
        # Europe showed only the bridge.
        self.assertEqual(got, [("europe", "Porto's new bridge opens"), ("world", "Lisbon trams back"),
                               ("world", "Ocean survey ends")])

    def test_a_story_already_shown_or_sent_recently_stays_out(self):
        news = [self.pick(self.stories[0], "Trams are back")]
        self.sent = [{"link": "https://news.example.test/ocean"}]
        got = [h["headline"] for h in daily._fill_empty_regions(SETTINGS, self.stories, news, None)]
        self.assertEqual(got, ["Trams are back", "Moon mission waits"])

    def test_nothing_new_leaves_the_region_empty_and_full_regions_alone(self):
        self.sent = [{"link": s.link} for s in self.stories]
        news = [self.pick(self.stories[0], "Trams are back")]
        self.assertEqual(daily._fill_empty_regions(SETTINGS, self.stories, news, None), news)
        both = news + [self.pick(self.stories[3], "Survey ends")]
        self.assertEqual(daily._fill_empty_regions(SETTINGS, self.stories, both, None), both)


if __name__ == "__main__":
    unittest.main()
