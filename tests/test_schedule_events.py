"""Derived schedule times preserve publication coverage, provenance and boundaries."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from component_loader import load_component_module

mod = load_component_module("schedule_events")
contract = load_component_module("provider")
status = load_component_module("schedule_status")


class ScheduleEventsTests(unittest.TestCase):
    def setUp(self):
        self.day = datetime(2026, 10, 24, tzinfo=mod.KYIV_TZ)
        self.now = self.day + timedelta(hours=22)
        self.schedule = contract.ScheduleReading(
            contract.Address("dtek_oem", "City", "Street", "12"),
            tuple(contract.Group(g, g) for g in ("A", "B")), (),
            tuple(
                contract.Coverage(g, kind, self.day + timedelta(days=d), self.day + timedelta(days=d + 1))
                for g in ("A", "B") for kind in ("outage", "possible") for d in (0, 1)
            ),
            self.now, self.now, contract.Publication(True, True, True, True), "current",
        )
        self.coord = SimpleNamespace(
            schedule=self.schedule, schedule_state="current", notifications_ready=True,
            address_error=None, schedule_error=None, schedule_reason=None,
        )

    def interval(self, start, end, group="A", kind="outage", source="daily"):
        return contract.Interval(group, kind, self.day + timedelta(hours=start),
                                 self.day + timedelta(hours=end), source)

    def set_intervals(self, *intervals):
        self.coord.schedule = mod.merge_schedule_intervals(replace(self.schedule, intervals=intervals))

    def event(self, key="next_planned_outage", groups=None, now=None):
        return mod.scheduled_event(self.coord, key, groups, now or self.now)

    def test_midnight_merge_is_chronological_and_preserves_other_groups(self):
        self.set_intervals(self.interval(24, 26), self.interval(23, 24), self.interval(22.5, 24, "B"))
        intervals = self.coord.schedule.intervals
        self.assertEqual(len(intervals), 2)
        self.assertEqual(intervals[0].group_id, "B")
        self.assertEqual(intervals[1], self.interval(23, 26))
        self.assertEqual(len(self.schedule.intervals), 0)  # immutable input
        self.assertEqual(self.coord.schedule, mod.merge_schedule_intervals(self.coord.schedule))

    def test_never_merge_different_groups_types_or_sources(self):
        for second in (
            self.interval(24, 26, "B"), self.interval(24, 26, kind="possible"),
            self.interval(24, 26, source="weekly"), self.interval(24.5, 26),
        ):
            with self.subTest(second=second):
                self.set_intervals(self.interval(23, 24), second)
                self.assertEqual(len(self.coord.schedule.intervals), 2)

    def test_missing_coverage_or_gap_prevents_merge(self):
        for coverage in (
            self.schedule.coverage[:1],
            tuple(replace(c, start=c.start + timedelta(seconds=1)) if c.start > self.now else c
                  for c in self.schedule.coverage),
        ):
            with self.subTest(coverage=coverage):
                self.coord.schedule = mod.merge_schedule_intervals(replace(
                    self.schedule, intervals=(self.interval(23, 24), self.interval(24, 26)),
                    coverage=coverage,
                ))
                self.assertEqual(len(self.coord.schedule.intervals), 2)

    def test_ongoing_cross_midnight_event_is_not_a_new_outage(self):
        self.set_intervals(self.interval(23, 24), self.interval(24, 26), self.interval(30, 32))
        for hour in (23, 24, 25, 26):
            with self.subTest(hour=hour):
                now = self.day + timedelta(hours=hour)
                self.assertEqual(self.event(now=now).value, self.day + timedelta(hours=30))
        self.assertEqual(self.event("scheduled_outage_end", now=self.day + timedelta(hours=24)).value,
                         self.day + timedelta(hours=26))
        # At the exclusive end, the sensor moves to the next event's end.
        self.assertEqual(self.event("scheduled_outage_end", now=self.day + timedelta(hours=26)).value,
                         self.day + timedelta(hours=32))

    def test_building_and_apartment_earliest_events_are_distinct(self):
        self.set_intervals(self.interval(25, 28), self.interval(23, 27, "B"))
        self.assertEqual(self.event().groups, ("B",))
        self.assertEqual(self.event(groups=["A"]).value, self.day + timedelta(hours=25))
        self.assertEqual(self.event("scheduled_outage_end").value, self.day + timedelta(hours=27))
        self.assertEqual(self.event(groups=[]).status, "no_groups")
        self.assertEqual(self.event(groups=["foreign"]).status, "missing_coverage")

    def test_overlapping_groups_end_reports_earliest_ongoing_group_not_whole_building(self):
        self.set_intervals(self.interval(21, 25), self.interval(20, 24, "B"))
        event = self.event("scheduled_outage_end")
        self.assertEqual(event.value, self.day + timedelta(hours=24))
        self.assertEqual(event.groups, ("B",))
        self.assertTrue(event.ongoing)

    def test_tied_start_contains_all_affected_groups(self):
        self.set_intervals(self.interval(23, 26), self.interval(23, 25, "B"))
        self.assertEqual(self.event().groups, ("A", "B"))
        self.assertEqual(self.event("scheduled_outage_end").groups, ("B",))

    def test_unpublished_next_day_does_not_promise_midnight_restoration(self):
        self.schedule = replace(self.schedule, coverage=tuple(c for c in self.schedule.coverage if c.start == self.day))
        self.set_intervals(self.interval(23, 24))
        self.assertEqual(self.event().value, self.day + timedelta(hours=23))
        end = self.event("scheduled_outage_end")
        self.assertIsNone(end.value)
        self.assertEqual(end.status, "end_unconfirmed")
        self.assertTrue(end.available)
        self.assertEqual(end.coverage_end, self.day + timedelta(days=1))
        self.assertFalse(self.event(now=self.day + timedelta(days=1)).available)

    def test_continuation_with_different_source_does_not_claim_power_returns(self):
        self.set_intervals(self.interval(23, 24), self.interval(24, 26, source="other"))
        self.assertEqual(self.event("scheduled_outage_end").status, "end_unconfirmed")

    def test_gaps_bound_the_search_even_when_later_outages_are_known(self):
        self.schedule = replace(self.schedule, coverage=tuple(
            replace(c, start=c.start + timedelta(hours=1)) if c.start > self.now else c
            for c in self.schedule.coverage
        ))
        self.set_intervals(self.interval(26, 28))
        event = self.event()
        self.assertEqual(event.status, "no_event_in_coverage")
        self.assertEqual(event.coverage_end, self.day + timedelta(days=1))
        self.assertIsNone(event.value)

    def test_missing_one_building_group_does_not_allow_a_building_prediction(self):
        self.schedule = replace(self.schedule, coverage=tuple(c for c in self.schedule.coverage if c.group_id == "A"))
        self.set_intervals(self.interval(23, 26))
        self.assertFalse(self.event().available)
        self.assertTrue(self.event(groups=["A"]).available)

    def test_weekly_forecast_cannot_become_planned_or_restoration(self):
        self.schedule = replace(self.schedule, coverage=tuple(c for c in self.schedule.coverage if c.kind == "possible"))
        self.set_intervals(self.interval(23, 26, kind="possible", source="weekly"))
        for key in ("next_planned_outage", "scheduled_outage_end"):
            self.assertFalse(self.event(key).available)
        possible = self.event("next_possible_outage")
        self.assertEqual(possible.sources, ("weekly",))
        self.assertEqual(possible.value, self.day + timedelta(hours=23))

    def test_failures_and_hidden_publication_do_not_reuse_saved_events(self):
        self.set_intervals(self.interval(23, 26))
        for field, value in (
            ("address_error", "offline"), ("schedule_error", "offline"),
            ("notifications_ready", False), ("schedule_state", "unpublished"),
        ):
            with self.subTest(field=field):
                previous = getattr(self.coord, field)
                setattr(self.coord, field, value)
                self.assertFalse(self.event().available)
                self.assertIsNone(self.event().value)
                setattr(self.coord, field, previous)

    def test_verified_empty_and_missing_coverage_are_different(self):
        empty = self.event()
        self.assertTrue(empty.available)
        self.assertEqual(empty.status, "no_event_in_coverage")
        self.coord.schedule = replace(self.schedule, coverage=())
        self.assertFalse(self.event().available)
        self.assertEqual(self.event().status, "missing_coverage")

    def test_clock_includes_events_coverage_edges_and_kyiv_midnight(self):
        self.set_intervals(self.interval(23, 24), self.interval(24, 26))
        for now, expected in ((22, 23), (23, 24), (24, 26), (26, 48)):
            self.assertEqual(
                mod.next_schedule_boundary(self.coord.schedule, self.day + timedelta(hours=now)),
                (self.day + timedelta(hours=expected)).astimezone(timezone.utc),
            )

    def test_clock_midnight_uses_actual_dst_day_length(self):
        for day, hours in ((datetime(2026, 3, 29, tzinfo=mod.KYIV_TZ), 23),
                           (datetime(2026, 10, 25, tzinfo=mod.KYIV_TZ), 25)):
            with self.subTest(day=day):
                boundary = mod.next_schedule_boundary(None, day)
                self.assertEqual(boundary - day.astimezone(timezone.utc), timedelta(hours=hours))

    def test_repeated_autumn_hour_does_not_reschedule_an_outage_already_started(self):
        start = datetime(2026, 10, 25, 3, 30, tzinfo=mod.KYIV_TZ, fold=0)
        end = datetime(2026, 10, 25, 4, tzinfo=mod.KYIV_TZ)
        now = datetime(2026, 10, 25, 3, 15, tzinfo=mod.KYIV_TZ, fold=1)
        self.set_intervals(contract.Interval("A", "outage", start, end, "daily"))
        self.assertEqual(self.event(now=now).status, "no_event_in_coverage")
        self.assertEqual(self.event("scheduled_outage_end", now=now).value, end)

    def test_tomorrow_status_tracks_full_group_coverage_in_kyiv(self):
        self.assertEqual(status.schedule_status(self.coord, now=self.now, day_offset=1), "empty")
        self.set_intervals(self.interval(25, 26))
        utc = self.now.astimezone(timezone.utc)
        self.assertEqual(status.schedule_status(self.coord, now=utc, day_offset=1), "current")
        self.assertEqual(status.schedule_status(self.coord, ["B"], utc, day_offset=1), "empty")
        attrs = status.status_attributes(self.coord, now=utc, day_offset=1)
        self.assertEqual(attrs["status_date"], "2026-10-25")
        self.assertEqual(attrs["schedule_status"], "current")

    def test_tomorrow_partial_forecast_missing_and_error_are_not_empty(self):
        for coverage, expected in (
            (tuple(c for c in self.schedule.coverage if c.group_id == "A"), "partial"),
            (tuple(c for c in self.schedule.coverage if c.kind == "possible"), "forecast"),
            ((), "missing"),
        ):
            self.coord.schedule = replace(self.schedule, coverage=coverage)
            self.assertEqual(status.schedule_status(self.coord, now=self.now, day_offset=1), expected)
        self.coord.schedule_error = "offline"
        self.assertEqual(status.schedule_status(self.coord, now=self.now, day_offset=1), "error")
