"""Unit tests for pure schedule-domain helpers."""

from __future__ import annotations

import datetime as dt
import unittest

from component_loader import load_component_module

schedule = load_component_module("schedule")


class ScheduleDomainTests(unittest.TestCase):
    """Regression tests for schedule merging and next-event lookup."""

    def test_utc_and_kyiv_ranges_crossing_midnight_are_identical(self):
        for day in (dt.date(2026, 10, 18), dt.date(2026, 10, 25), dt.date(2026, 3, 29)):
            start = dt.datetime.combine(day, dt.time(), tzinfo=schedule.KYIV_TZ)
            end = start + dt.timedelta(hours=6)
            preset = {"GPV5.1": {str(day.isoweekday()): {"1": "no", "2": "no", "4": "no"}}}
            local = schedule.get_schedule_windows_for_range(start, end, ["GPV5.1"], {}, preset)
            utc = schedule.get_schedule_windows_for_range(start.astimezone(dt.timezone.utc), end.astimezone(dt.timezone.utc), ["GPV5.1"], {}, preset)
            self.assertEqual(utc, local)
            self.assertTrue(utc)

    def test_groups_remain_separate_even_for_overlapping_windows(self):
        date = dt.date(2026, 10, 17)
        preset = {"GPV3.1": {"6": {"11": "no", "12": "no"}}, "GPV5.1": {"6": {"12": "no", "13": "no"}}}
        windows = schedule.get_schedule_windows_for_date(date, ["GPV5.1", "GPV3.1"], {}, preset)
        self.assertEqual(len(windows), 2)
        self.assertIn("group=GPV3.1", windows[0].description)
        self.assertIn("group=GPV5.1", windows[1].description)
        self.assertEqual([(w.start.hour, w.end.hour) for w in windows], [(10, 12), (11, 13)])

    def test_fact_slots_trim_overlapping_preset_slots(self) -> None:
        """Preset windows should be cut around overlapping fact windows."""
        date = dt.date(2026, 3, 9)
        day_timestamp = str(
            int(dt.datetime(2026, 3, 9, 0, 0, tzinfo=schedule.KYIV_TZ).timestamp())
        )
        fact = {
            day_timestamp: {
                "GPV5.1": {
                    "9": "first",
                }
            }
        }
        preset = {
            "GPV5.1": {
                "1": {
                    "9": "maybe",
                }
            }
        }

        windows = schedule.get_schedule_windows_for_date(
            date,
            ["GPV5.1"],
            fact,
            preset,
            event_type="possible",
        )

        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].start.hour, 8)
        self.assertEqual(windows[0].start.minute, 30)
        self.assertEqual(windows[0].end.hour, 9)
        self.assertEqual(windows[0].end.minute, 0)

    def test_next_event_search_scans_entire_week(self) -> None:
        """Calendar helper should find the next event beyond a 48-hour horizon."""
        now = dt.datetime(2026, 3, 9, 12, 0, tzinfo=schedule.KYIV_TZ)
        preset = {
            "GPV5.1": {
                "5": {
                    "10": "maybe",
                }
            }
        }

        window = schedule.find_current_or_next_window(
            now,
            ["GPV5.1"],
            {},
            preset,
            event_type="possible",
        )

        self.assertIsNotNone(window)
        self.assertEqual(window.start.date(), dt.date(2026, 3, 13))
        self.assertEqual(window.start.hour, 9)
        self.assertEqual(window.end.hour, 10)


if __name__ == "__main__":
    unittest.main()
