"""Missing publication and failures are never reported as an outage-free day."""

from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest

from component_loader import load_component_module

mod = load_component_module("schedule_status")
contract = load_component_module("provider")


class ScheduleStatusTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 18, tzinfo=mod.KYIV_TZ)
        self.end = self.start + timedelta(days=1)
        self.coverage = tuple(
            contract.Coverage(g, "outage", self.start, self.end) for g in ("A", "B")
        )
        self.coord = SimpleNamespace(
            address_error=None,
            schedule_error=None,
            schedule_reason=None,
            schedule_state="current",
            notifications_ready=True,
            schedule=SimpleNamespace(
                groups=[contract.Group(g, g) for g in ("A", "B")],
                coverage=self.coverage,
                intervals=[],
            ),
        )

    def status(self, groups=None):
        return mod.schedule_status(self.coord, groups, self.start + timedelta(hours=12))

    def test_verified_empty_is_scoped_to_day_and_every_group(self):
        self.assertEqual(self.status(), "empty")
        self.coord.schedule.coverage = self.coverage[:1]
        self.assertEqual(self.status(), "partial")
        self.assertEqual(self.status(["A"]), "empty")
        self.assertEqual(self.status(["B"]), "missing")
        self.coord.schedule.coverage = tuple(
            replace(c, start=c.start + timedelta(days=1), end=c.end + timedelta(days=1))
            for c in self.coverage
        )
        self.assertEqual(self.status(), "missing")

    def test_no_gaps_allowed_and_adjacent_coverage_can_be_combined(self):
        first = replace(self.coverage[0], end=self.start + timedelta(hours=12))
        second = replace(first, start=first.end, end=self.end)
        self.coord.schedule.coverage = (first, second, self.coverage[1])
        self.assertEqual(self.status(), "empty")
        self.coord.schedule.coverage = (
            first,
            replace(second, start=second.start + timedelta(seconds=1)),
            self.coverage[1],
        )
        self.assertEqual(self.status(), "partial")

    def test_forecast_does_not_prove_no_planned_outages(self):
        self.coord.schedule.coverage = tuple(
            replace(c, kind="possible") for c in self.coverage
        )
        self.assertEqual(self.status(), "forecast")
        self.coord.schedule.coverage = ()
        self.assertEqual(self.status(), "missing")

    def test_selected_groups_do_not_borrow_other_groups_events(self):
        self.coord.schedule.intervals = [
            contract.Interval("B", "outage", self.start, self.end, "daily")
        ]
        self.assertEqual(self.status(), "current")
        self.assertEqual(self.status(["A"]), "empty")
        self.assertEqual(self.status(["B"]), "current")

    def test_request_failure_has_priority_over_publication_and_saved_events(self):
        for reason in ("missing", "unpublished", "unsupported"):
            self.coord.schedule_state = reason
            self.coord.notifications_ready = False
            self.assertEqual(self.status(), reason)
            self.coord.schedule_error = "timeout"
            self.assertEqual(self.status(), "error")
            self.coord.schedule_error = None
        self.coord.address_error = "invalid address"
        self.assertEqual(self.status(), "error")
