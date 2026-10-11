"""Unit: the weekly letter goes out on email.weekly_day, Sunday unless set.
Run with: PYTHONPATH=src .venv/bin/python -m unittest discover tests"""

import unittest
from datetime import date

from butler import weekly


class LetterDayTest(unittest.TestCase):
    def test_sunday_unless_set(self):
        self.assertEqual(weekly.letter_day_name({}), "Sunday")
        self.assertEqual(weekly.letter_day_name({"email": {"weekly_day": 4}}), "Friday")
        self.assertEqual(weekly.letter_day_name({"email": {"weekly_day": 0}}), "Monday")

    def test_next_letter_day_counts_today(self):
        wednesday = date(2026, 10, 14)
        self.assertEqual(weekly.next_letter_day({}, wednesday), date(2026, 10, 18))
        self.assertEqual(weekly.next_letter_day({"email": {"weekly_day": 4}}, wednesday), date(2026, 10, 16))
        self.assertEqual(weekly.next_letter_day({"email": {"weekly_day": 2}}, wednesday), wednesday)
        self.assertEqual(weekly.next_letter_day({"email": {"weekly_day": 1}}, wednesday), date(2026, 10, 20))

    def test_part_of_day(self):
        self.assertEqual([weekly._part_of_day(h) for h in (7, 12, 17, 18)],
                         ["morning", "afternoon", "afternoon", "evening"])


if __name__ == "__main__":
    unittest.main()
