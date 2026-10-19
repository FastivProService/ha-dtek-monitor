"""Address repairs remain independent of GPV schedules and unavailable data."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
import unittest

import test_ha_runtime as runtime
from component_loader import load_component_module

if runtime.HAS_HA:
    calendar = runtime.calendar
    works = load_component_module("address_works")


@unittest.skipUnless(runtime.HAS_HA, "Requires real Home Assistant runtime")
class AddressWorksTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.RuntimeTests.asyncSetUp(self)
        self.day = datetime.now(calendar.KYIV_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        self.entity = calendar.DTEKWorksCalendar(self.coord, self.entry)
        self.entity.hass = self.hass
        self.status = runtime.sensors.DTEKWorksStatusSensor(self.coord, self.entry)

    def report(self, start=8, end=17, **kwargs):
        return {
            "type": "1", "sub_type": "",
            "start_date": (self.day + timedelta(hours=start)).strftime("%H:%M %d.%m.%Y") if start is not None else "",
            "end_date": (self.day + timedelta(hours=end)).strftime("%H:%M %d.%m.%Y") if end is not None else "",
            "sub_type_reason": ["GPV5.1"], **kwargs,
        }

    def records(self, *records):
        self.client.get_home_status.return_value["data"]["12А"] = list(records)

    async def events(self):
        return await self.entity.async_get_events(self.hass, self.day, self.day + timedelta(days=2))

    async def test_planned_report_is_a_separate_calendar_even_when_groups_are_unpublished(self):
        self.records(self.report())
        self.client.get_home_status.return_value["showCurSchedule"] = False
        await self.coord.async_refresh()
        self.assertTrue(self.entity.available)
        self.assertFalse(self.coord.notifications_ready)
        self.assertEqual(self.status.native_value, "reported")
        event, = await self.events()
        self.assertEqual(event.start, self.day + timedelta(hours=8))
        self.assertEqual(event.end, self.day + timedelta(hours=17))
        self.assertIn("Планові ремонтні роботи", event.description)
        self.assertFalse(self.entity.extra_state_attributes["advance_notice_guaranteed"])

    async def test_emergency_primary_does_not_hide_multiple_repairs_and_duplicates_are_removed(self):
        self.records(
            self.report(), self.report(), self.report(18, 20, sub_type="Заміна обладнання"),
            self.report(type="2", sub_type="Аварійне відключення"),
            self.report(type="2", sub_type="Стабілізаційне відключення"),
        )
        await self.coord.async_refresh()
        self.assertEqual(self.coord.address_data["outage_type"], "emergency")
        events = await self.events()
        self.assertEqual(len(events), 2)
        self.assertEqual([e.start.hour for e in events], [8, 18])

    async def test_unknown_times_do_not_turn_into_empty_or_invented_events(self):
        for report in (self.report(end=None), self.report(start=None), self.report(start=None, end=None, sub_type="Ремонт")):
            with self.subTest(report=report):
                self.records(self.report(), report)
                await self.coord.async_refresh()
                self.assertTrue(self.coord.address_success)
                self.assertFalse(self.entity.available)
                self.assertEqual(self.status.native_value, "incomplete")
                self.assertIsNone(self.entity.event)
                self.assertEqual(self.entity.extra_state_attributes["complete_interval_count"], 1)
                with self.assertRaises(runtime.HomeAssistantError):
                    await self.events()

    async def test_invalid_dated_description_is_incomplete_not_a_calendar_window(self):
        for fields in (
            {"start_date": "bad"},
            {"start_date": self.day.strftime("17:00 %d.%m.%Y"), "end_date": self.day.strftime("08:00 %d.%m.%Y")},
        ):
            self.records(self.report(sub_type="Ремонт", **fields))
            await self.coord.async_refresh()
            self.assertFalse(self.entity.available)
            self.assertEqual(self.status.native_value, "incomplete")

    async def test_cleared_repair_disappears_without_retaining_an_old_event(self):
        self.records(self.report())
        await self.coord.async_refresh()
        self.assertEqual(len(await self.events()), 1)
        self.records({"sub_type_reason": ["GPV5.1"]})
        await self.coord.async_refresh()
        self.assertTrue(self.entity.available)
        self.assertEqual(self.status.native_value, "empty")
        self.assertEqual(await self.events(), [])

    async def test_failure_makes_works_unavailable_then_explicit_empty_recovers(self):
        self.records(self.report())
        await self.coord.async_refresh()
        self.client.get_home_status.side_effect = runtime.client_module.DTEKApiError("offline")
        for _ in range(2):
            await self.coord.async_refresh()
            self.assertFalse(self.entity.available)
            self.assertEqual(self.status.native_value, "unavailable")
            with self.assertRaises(runtime.HomeAssistantError):
                await self.events()
        self.client.get_home_status.side_effect = None
        self.records({"sub_type_reason": ["GPV5.1"]})
        await self.coord.async_refresh()
        self.assertEqual(self.status.native_value, "empty")

    async def test_schedule_failure_does_not_hide_a_fresh_repair_report(self):
        self.records(self.report())
        self.client.check_update.side_effect = runtime.client_module.DTEKApiError("schedule offline")
        await self.coord.async_refresh()
        self.assertFalse(self.coord.last_update_success)
        self.assertTrue(self.entity.available)
        self.assertEqual(len(await self.events()), 1)

    async def test_utc_midnight_range_finds_local_work_and_end_boundary_is_exclusive(self):
        self.records(self.report(start=0, end=1))
        await self.coord.async_refresh()
        start = self.day.astimezone(timezone.utc)
        events = await self.entity.async_get_events(self.hass, start, start + timedelta(hours=1))
        self.assertEqual(len(events), 1)
        self.assertEqual(await self.entity.async_get_events(
            self.hass, start + timedelta(hours=1), start + timedelta(hours=2)
        ), [])
        self.assertEqual(await self.entity.async_get_events(
            self.hass, self.day.replace(tzinfo=None), (self.day + timedelta(hours=1)).replace(tzinfo=None)
        ), events)

    async def test_external_operator_is_not_a_confirmed_empty_repair_calendar(self):
        self.records(self.report(cek=True))
        await self.coord.async_refresh()
        self.assertFalse(self.entity.available)
        self.assertEqual(self.status.native_value, "unsupported")
        with self.assertRaises(runtime.HomeAssistantError):
            await self.events()

    async def test_entity_is_added_once_to_building_with_distinct_identity(self):
        self.entry.runtime_data = self.coord
        add = Mock()
        await calendar.async_setup_entry(self.hass, self.entry, add)
        entities = add.call_args.args[0]
        self.assertEqual(sum(isinstance(e, calendar.DTEKWorksCalendar) for e in entities), 1)
        self.assertEqual(len({e.unique_id for e in entities}), len(entities))

    async def test_calendar_lookup_uses_no_extra_dtek_requests(self):
        self.records(self.report(start=0, end=48))
        await self.coord.async_refresh()
        before = self.client.get_home_status.await_count
        self.assertIsNotNone(self.entity.event)
        await self.events()
        await self.events()
        self.assertEqual(self.client.get_home_status.await_count, before)
