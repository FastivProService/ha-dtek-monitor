"""Exercise calendar merging, HA states, local clock and private group sensors."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, AsyncMock, patch

import test_ha_runtime as runtime
import test_account_schedule as account_runtime
from component_loader import load_component_module

if runtime.HAS_HA:
    mod = load_component_module("schedule_events")
    statuses = load_component_module("schedule_status")
    setup = load_component_module("__init__")


@unittest.skipUnless(runtime.HAS_HA, "Requires real Home Assistant")
class ScheduleEventsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.RuntimeTests.asyncSetUp(self)
        self.day = datetime.now(mod.KYIV_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        self.now = self.day + timedelta(hours=22)
        self.clock = Mock(wraps=datetime)
        self.clock.now.side_effect = lambda tz=None: self.now.astimezone(tz) if tz else self.now.replace(tzinfo=None)
        for module in (mod, statuses, load_component_module("snapshot")):
            patcher = patch.object(module, "datetime", self.clock)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(runtime.coordinator, "dt", SimpleNamespace(datetime=self.clock, timezone=timezone))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.schedule["fact"] = {
            "update": self.day.strftime("%d.%m.%Y 12:00"),
            "data": {
                str(int((self.day + timedelta(days=d)).timestamp())): {
                    "GPV5.1": {str(h): ("no" if h in hours else "yes") for h in range(1, 25)}
                }
                for d, hours in ((0, (24,)), (1, (1, 2)))
            },
        }
        await self.coord.async_refresh()
        self.assertTrue(self.coord.notifications_ready)
        self.addCleanup(self.coord.async_stop_schedule_clock)

    async def add_entity(self, entity, key):
        entity.hass = self.hass
        entity.entity_id = "sensor.dtek_test_" + key
        entity.platform = runtime.EntityPlatform(
            hass=self.hass, logger=logging.getLogger(__name__), domain="sensor",
            platform_name="dtek_monitor", platform=None,
            scan_interval=timedelta(seconds=30), entity_namespace=None,
        )
        entity.add_to_platform_start(self.hass, entity.platform, None)
        await entity.add_to_platform_finish()
        self.addAsyncCleanup(entity.async_remove)
        return entity

    async def test_live_calendar_and_saved_snapshot_merge_midnight_before_utc_range_filter(self):
        start = (self.day + timedelta(days=1)).astimezone(timezone.utc)
        events = self.coord.get_events_for_range(start, start + timedelta(hours=2), ["GPV5.1"], "outage")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].start, self.day + timedelta(hours=23))
        self.assertEqual(events[0].end, self.day + timedelta(hours=26))
        self.assertEqual(events[0].description, "Planned; group=GPV5.1")
        self.assertEqual(len([w for w in self.coord.snapshot.windows if w.event_type == "outage"]), 1)
        # Snapshot can still be serialized and restored with a crossing interval.
        stored = self.coord.snapshot.to_dict()
        self.coord.snapshot = None
        self.coord._snapshot_store.async_load = AsyncMock(return_value=stored)
        await self.coord.async_load_snapshot()
        self.assertEqual(len([w for w in self.coord.snapshot.windows if w.event_type == "outage"]), 1)

    async def test_calendar_titles_follow_ha_language_without_changing_bot_provenance(self):
        for language, title, label in (
            ("en", "Planned outage", "Saved schedule"),
            ("uk", "Планове відключення", "Збережений графік"),
            ("de", "Planned outage", "Saved schedule"),
        ):
            with self.subTest(language=language):
                self.hass.config.language = language
                events = self.coord.get_events_for_range(self.day, self.day + timedelta(days=2), ["GPV5.1"], "outage")
                self.assertEqual(events[0].summary, title)
                self.assertEqual(events[0].description, "Planned; group=GPV5.1")
                saved = runtime.calendar.DTEKSnapshotCalendar(self.coord, self.entry)
                saved_events = await saved.async_get_events(self.hass, self.day, self.day + timedelta(days=2))
                self.assertEqual(saved_events[0].summary, f"[{label}] {title}")
                self.assertIn("usage=display_only", saved_events[0].description)
                possible = self.coord.schedule.intervals[0].as_window()
                possible = replace(possible, event_type="possible", description="Possible; group=GPV5.1")
                event = runtime.coordinator._window_to_calendar_event(possible, language)
                self.assertEqual(event.summary, "Можливе відключення" if language == "uk" else "Possible outage")
                self.assertEqual(event.description, possible.description)

    async def test_clock_changes_real_ha_states_without_network_or_freshness_changes(self):
        await runtime.load_registries(self.hass)
        entity = await self.add_entity(runtime.sensors.DTEKScheduleEventSensor(self.coord, self.entry, "next_planned_outage"), "next")
        tomorrow = await self.add_entity(runtime.sensors.DTEKScheduleStatusSensor(self.coord, self.entry, day_offset=1), "tomorrow")
        self.assertEqual(datetime.fromisoformat(self.hass.states.get(entity.entity_id).state), self.day + timedelta(hours=23))
        self.assertEqual(self.hass.states.get(tomorrow.entity_id).state, "current")
        before = (self.coord.last_success, self.coord.snapshot.checked_at, self.client.get_home_status.await_count)
        scheduled = []
        def track(hass, callback, when):
            scheduled.append((callback, when))
            return Mock()
        with patch.object(runtime.coordinator, "async_track_point_in_utc_time", side_effect=track):
            self.coord.async_start_schedule_clock()
            self.assertEqual(scheduled[-1][1], (self.day + timedelta(hours=23)).astimezone(timezone.utc))
            self.now = self.day + timedelta(hours=23)
            scheduled[-1][0](self.now.astimezone(timezone.utc))
            self.assertEqual(self.hass.states.get(entity.entity_id).state, "unknown")
            self.assertEqual(self.hass.states.get(entity.entity_id).attributes["event_status"], "no_event_in_coverage")
            self.now = self.day + timedelta(days=1)
            scheduled[-1][0](self.now.astimezone(timezone.utc))
            self.assertEqual(self.hass.states.get(tomorrow.entity_id).state, "forecast")
            self.assertEqual(self.hass.states.get(tomorrow.entity_id).attributes["status_date"], (self.day + timedelta(days=2)).date().isoformat())
        self.assertEqual(before, (self.coord.last_success, self.coord.snapshot.checked_at, self.client.get_home_status.await_count))

    async def test_errors_remove_timestamp_and_recovery_restores_it_in_ha(self):
        await runtime.load_registries(self.hass)
        entity = await self.add_entity(runtime.sensors.DTEKScheduleEventSensor(self.coord, self.entry, "scheduled_outage_end"), "end")
        self.assertEqual(datetime.fromisoformat(self.hass.states.get(entity.entity_id).state), self.day + timedelta(hours=26))
        self.client.get_home_status.side_effect = runtime.client_module.DTEKApiError("offline")
        for _ in range(2):
            await self.coord.async_refresh()
            self.assertEqual(self.hass.states.get(entity.entity_id).state, "unavailable")
        self.client.get_home_status.side_effect = None
        await self.coord.async_refresh()
        self.assertEqual(datetime.fromisoformat(self.hass.states.get(entity.entity_id).state), self.day + timedelta(hours=26))

    async def test_clock_reschedules_after_new_publication_and_unload_cancels_it(self):
        cancelled = []
        def track(*args):
            cancel = Mock()
            cancelled.append(cancel)
            return cancel
        with patch.object(runtime.coordinator, "async_track_point_in_utc_time", side_effect=track):
            self.coord.async_start_schedule_clock()
            await self.coord.async_refresh()
            cancelled[0].assert_called_once()
            self.assertEqual(len(cancelled), 2)
            self.hass.config_entries = SimpleNamespace(async_unload_platforms=AsyncMock(return_value=True))
            self.entry.runtime_data = self.coord
            self.provider.close = AsyncMock()
            self.assertTrue(await setup.async_unload_entry(self.hass, self.entry))
            cancelled[1].assert_called_once()
            self.assertFalse(self.coord._clock_started)
            self.assertIsNone(self.coord._cancel_clock)

    async def test_setup_adds_unique_translated_entities_with_native_timestamp_class(self):
        self.entry.runtime_data = self.coord
        add = Mock()
        await runtime.sensors.async_setup_entry(self.hass, self.entry, add)
        entities = add.call_args.args[0]
        self.assertEqual(len({e.unique_id for e in entities}), len(entities))
        keys = {e.translation_key for e in entities}
        self.assertTrue(set(mod.EVENT_SENSOR_KEYS).issubset(keys))
        self.assertIn("schedule_tomorrow_status", keys)
        for entity in entities:
            if entity.translation_key in mod.EVENT_SENSOR_KEYS:
                self.assertEqual(entity.device_class, "timestamp")
        for lang in ("en", "uk"):
            data = json.loads((Path(__file__).parents[1] / "custom_components/dtek_monitor/translations" / f"{lang}.json").read_text())
            self.assertTrue(keys.issubset(data["entity"]["sensor"]))


@unittest.skipUnless(runtime.HAS_HA, "Requires real Home Assistant")
class AccountEventSensorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await account_runtime.AccountScheduleTests.asyncSetUp(self)

    def link(self, groups=None):
        account_runtime.AccountScheduleTests.link(self, groups)

    async def test_private_event_follows_group_selection_without_narrowing_building(self):
        module = account_runtime.mod
        self.link()
        now = datetime.now(timezone.utc)
        self.coord.schedule = replace(self.coord.schedule, intervals=tuple(
            account_runtime.contract.Interval(g, "outage", now + timedelta(minutes=minutes), now + timedelta(minutes=minutes + 10), "daily")
            for g, minutes in (("GPV3.1", 30), ("GPV5.1", 10))
        ), coverage=tuple(replace(c, start=now - timedelta(hours=1), end=now + timedelta(hours=2)) for c in self.coord.schedule.coverage))
        entity = module.AccountScheduleEventSensor(self.personal, "100001", "next_planned_outage")
        entity.hass = self.hass
        self.assertEqual(entity.native_value, now + timedelta(minutes=30))
        self.assertEqual(entity.extra_state_attributes["event_groups"], ["GPV3.1"])
        self.assertEqual(mod.scheduled_event(self.coord, "next_planned_outage").value, now + timedelta(minutes=10))
        self.link(["GPV5.1"])
        self.assertEqual(entity.native_value, now + timedelta(minutes=10))
        self.assertEqual(len(self.coord.schedule.groups), 2)
        self.link([])  # Multiple building groups: do not guess an apartment group.
        self.assertFalse(entity.available)
        self.assertIsNone(entity.native_value)

    async def test_personal_tomorrow_status_requires_a_group_and_uses_its_coverage(self):
        entity = account_runtime.mod.AccountScheduleStatusSensor(self.personal, "100001", day_offset=1)
        entity.hass = self.hass
        self.assertEqual(entity.native_value, "no_address")
        self.link([])
        self.assertEqual(entity.native_value, "not_selected")
        self.link()
        now = datetime.now(mod.KYIV_TZ)
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        self.coord.schedule = replace(self.coord.schedule, intervals=(), coverage=(
            account_runtime.contract.Coverage("GPV3.1", "outage", tomorrow, tomorrow + timedelta(days=1)),
        ))
        self.assertEqual(entity.native_value, "schedule_empty")
        self.link(["GPV5.1"])
        self.assertEqual(entity.native_value, "schedule_missing")
