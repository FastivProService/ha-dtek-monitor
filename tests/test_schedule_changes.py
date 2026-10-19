"""Semantic change detection never equates missing coverage with cancellations."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from component_loader import load_component_module

mod = load_component_module("schedule_changes")
contract = load_component_module("provider")


class ScheduleChangesTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 19, 8, tzinfo=timezone.utc)
        self.start = self.now.replace(hour=0)
        self.end = self.start + timedelta(days=1)
        self.record = {
            "coverage": [["GPV3.1", self.start.timestamp(), self.end.timestamp()]],
            "intervals": [["GPV3.1", "outage", (self.now + timedelta(hours=1)).timestamp(), (self.now + timedelta(hours=3)).timestamp()]],
        }

    def changes(self, record, now=None):
        return mod.changed_groups(self.record, record, now or self.now)

    def test_initial_same_and_expired_intervals_are_silent(self):
        self.assertEqual(mod.changed_groups(None, self.record, self.now), [])
        self.assertEqual(self.changes(deepcopy(self.record)), [])
        empty = {**self.record, "intervals": []}
        self.assertEqual(self.changes(empty), ["GPV3.1"])
        self.assertEqual(self.changes(empty, self.now + timedelta(hours=4)), [])
        for record in (None, {"coverage": [], "intervals": []}):
            self.assertEqual(self.changes(record), [])

    def test_future_change_current_end_extension_and_confirmed_cancellation(self):
        changed = deepcopy(self.record)
        changed["intervals"][0][3] += 1800
        for now in (self.now, self.now + timedelta(hours=2)):
            self.assertEqual(self.changes(changed, now), ["GPV3.1"])
        past_start = deepcopy(self.record)
        past_start["intervals"][0][2] -= 900
        self.assertEqual(self.changes(past_start, self.now + timedelta(hours=2)), [])

    def test_publishing_empty_tomorrow_counts_but_missing_day_does_not(self):
        published = deepcopy(self.record)
        published["coverage"].append(["GPV3.1", self.end.timestamp(), self.end.timestamp() + 86400])
        self.assertEqual(self.changes(published), ["GPV3.1"])
        self.assertEqual(mod.changed_groups(published, self.record, self.now), [])
        retained = mod.remember(published, self.record, self.now)
        self.assertEqual(mod.changed_groups(retained, published, self.now), [])

    def test_order_merge_and_midnight_window_rollover_are_silent(self):
        crossing = deepcopy(self.record)
        crossing["coverage"].append(["GPV3.1", self.end.timestamp(), self.end.timestamp() + 86400])
        crossing["intervals"] = [["GPV3.1", "outage", self.end.timestamp() - 3600, self.end.timestamp() + 7200]]
        split = deepcopy(crossing)
        split["intervals"] = [
            ["GPV3.1", "outage", self.end.timestamp(), self.end.timestamp() + 7200],
            ["GPV3.1", "outage", self.end.timestamp() - 3600, self.end.timestamp()],
        ]
        split["coverage"].reverse()
        self.assertEqual(mod.changed_groups(crossing, split, self.now), [])
        split["coverage"] = split["coverage"][:1]
        split["intervals"] = split["intervals"][:1]
        self.assertEqual(mod.changed_groups(crossing, split, self.end + timedelta(minutes=5)), [])

    def test_incomplete_coverage_cannot_cancel_an_outage_outside_verified_range(self):
        partial = {"coverage": [["GPV3.1", self.start.timestamp(), self.now.timestamp() + 600]], "intervals": []}
        self.assertEqual(self.changes(partial), [])
        retained = mod.remember(self.record, partial, self.now)
        self.assertEqual(mod.changed_groups(retained, self.record, self.now), [])

    def test_group_reassignment_is_not_a_schedule_change(self):
        other = deepcopy(self.record)
        for row in other["coverage"] + other["intervals"]:
            row[0] = "GPV5.1"
        self.assertEqual(self.changes(other), [])

    def test_record_ignores_poll_time_source_timestamp_and_weekly_forecasts(self):
        address = contract.Address("dtek_oem", "Test", "Example", "1")
        reading = contract.ScheduleReading(address, (contract.Group("GPV3.1", "3.1"),),
            (contract.Interval("GPV3.1", "outage", self.now, self.end, "daily"),),
            (contract.Coverage("GPV3.1", "outage", self.start, self.end),),
            self.now, self.now, contract.Publication(True, True, True, True), "current")
        baseline = mod.schedule_record(reading)
        self.assertEqual(mod.schedule_record(replace(reading, fetched_at=self.end, source_updated_at=self.end)), baseline)
        self.assertIsNone(mod.schedule_record(replace(reading, coverage=(replace(reading.coverage[0], kind="possible"),))))
        self.assertIsNone(mod.schedule_record(replace(reading, state="missing")))
        self.assertEqual(mod.revision(baseline), mod.revision(deepcopy(baseline)))

    def test_rejected_corrupt_baselines(self):
        for value in ({}, [], {"coverage": [["g", 1, float("inf")]], "intervals": []}, {"coverage": [["g", 2, 1]], "intervals": []}):
            with self.assertRaises((ValueError, KeyError, TypeError, OverflowError)):
                mod.validate_record(value)
