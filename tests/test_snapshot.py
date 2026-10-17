"""Persistent display snapshot contract and finite coverage."""

import copy
import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from component_loader import load_component_module

m = load_component_module("snapshot")
schedule = load_component_module("schedule")


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
        self.snapshot = m.ScheduleSnapshot(
            "dtek_oem",
            ("Одеса", "street", "1"),
            ("GPV3.1", "GPV5.1"),
            self.now,
            "17.10.2026 12:00",
            self.now - timedelta(hours=12),
            self.now + timedelta(days=7),
            (
                schedule.ScheduleWindow(
                    "outage",
                    self.now,
                    self.now + timedelta(hours=1),
                    "Outage",
                    "Planned; group=GPV3.1",
                ),
            ),
        )

    def restore(self, payload, **kw):
        return m.ScheduleSnapshot.from_dict(
            payload,
            provider_id="dtek_oem",
            address=("Одеса", "street", "1"),
            now=self.now,
            **kw
        )

    def test_roundtrip_preserves_identity_groups_and_times(self):
        restored = self.restore(self.snapshot.to_dict())
        self.assertEqual(restored, self.snapshot)
        self.assertEqual(restored.snapshot_id, self.snapshot.snapshot_id)

    def test_unchanged_recheck_updates_time_without_changing_content_identity(self):
        fresh = replace(self.snapshot, checked_at=self.now + timedelta(minutes=5))
        self.assertEqual(fresh.snapshot_id, self.snapshot.snapshot_id)
        self.assertNotEqual(
            replace(fresh, groups=("GPV5.1",)).snapshot_id, fresh.snapshot_id
        )

    def test_other_region_or_address_cannot_reuse_snapshot(self):
        for key, value in [
            ("provider_id", "dtek_kyiv"),
            ("address", ["Київ", "street", "1"]),
        ]:
            payload = self.snapshot.to_dict()
            payload[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.restore(payload)

    def test_corrupt_or_future_snapshot_is_rejected(self):
        values = [
            ("checked_at", "2026-10-17T12:00:00"),
            ("checked_at", "bad"),
            ("checked_at", "2030-01-01T12:00:00+00:00"),
            ("groups", []),
            ("groups", ["GPV5.1", "GPV3.1"]),
            ("windows", {}),
            ("coverage_end", "2040-01-01T00:00:00+00:00"),
        ]
        for key, value in values:
            payload = self.snapshot.to_dict()
            payload[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(
                (ValueError, TypeError)
            ):
                self.restore(payload)
        payload = self.snapshot.to_dict()
        payload["windows"][0]["description"] = "Planned; group=GPV9.9"
        with self.assertRaises(ValueError):
            self.restore(payload)

    def test_old_weekly_template_is_never_repeated(self):
        restored = self.restore(self.snapshot.to_dict())
        for weeks in [1, 2, 10]:
            start = self.now + timedelta(weeks=weeks)
            self.assertEqual(
                restored.events_between(start, start + timedelta(days=1)), []
            )
