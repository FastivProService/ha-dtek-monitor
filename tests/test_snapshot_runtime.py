"""Real HA storage, offline setup, failure and recovery scenarios."""

from __future__ import annotations
import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import test_ha_runtime as runtime_tests

HAS_HA = runtime_tests.HAS_HA
from types import SimpleNamespace
from component_loader import load_component_module

if HAS_HA:
    from homeassistant.exceptions import ConfigEntryNotReady
    from homeassistant.config_entries import ConfigEntryState

    coordinator = load_component_module("coordinator")
    calendar = load_component_module("calendar")
    sensors = load_component_module("sensor")
    binary = load_component_module("binary_sensor")
    client_module = load_component_module("dtek_client")
    setup_module = load_component_module("__init__")


@unittest.skipUnless(HAS_HA, "Requires real Home Assistant runtime")
class SnapshotRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime_tests.RuntimeTests.asyncSetUp(self)
        self.entry.state = ConfigEntryState.SETUP_IN_PROGRESS
        self.hass.config_entries = SimpleNamespace(
            async_forward_entry_setups=AsyncMock()
        )

    async def snapshot(self):
        day = datetime.now(coordinator.KYIV_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        self.day = day
        self.schedule["fact"]["data"] = {
            str(int(day.timestamp())): {
                "GPV5.1": {**{str(h): "yes" for h in range(1, 25)}, "14": "no"},
                "GPV3.1": {**{str(h): "yes" for h in range(1, 25)}, "10": "no"},
            }
        }
        await self.coord.async_refresh()
        self.assertTrue(self.coord.notifications_ready)
        return self.coord.snapshot

    async def test_restart_while_offline_restores_display_but_not_notifications(self):
        saved = await self.snapshot()
        restored = coordinator.DTEKDataCoordinator(self.hass, self.provider, self.entry)
        await restored.async_load_snapshot()
        self.assertEqual(saved, restored.snapshot)
        self.assertFalse(restored.notifications_ready)
        self.client.get_home_status.side_effect = client_module.DTEKApiError("offline")
        await restored.async_refresh()
        display = calendar.DTEKSnapshotCalendar(restored, self.entry)
        active = calendar.DTEKScheduleCalendar(
            restored, self.entry, "outage", "outage_schedule", "outage_schedule"
        )
        events = await display.async_get_events(
            self.hass, self.day, self.day + timedelta(days=1)
        )
        self.assertTrue(display.available)
        self.assertFalse(active.available)
        self.assertEqual(len(events), 1)
        self.assertIn("usage=display_only", events[0].description)
        self.assertIn("Актуальність не підтверджена", events[0].description)
        self.assertEqual(
            display.extra_state_attributes["last_verified_at"],
            saved.checked_at.isoformat(),
        )
        self.assertEqual(display.extra_state_attributes["data_status"], "stale")

    async def test_first_refresh_offline_with_cache_still_sets_up_platforms(self):
        await self.snapshot()
        self.client.get_home_status.side_effect = client_module.DTEKApiError("offline")
        self.client.close = AsyncMock()
        self.entry.add_update_listener = Mock(return_value=lambda: None)
        with (
            patch.object(
                setup_module, "_async_migrate_registry_identifiers", AsyncMock()
            ),
            patch.object(setup_module, "create_provider", return_value=self.provider),
            patch.object(
                self.hass.config_entries, "async_forward_entry_setups", AsyncMock()
            ) as forward,
        ):
            self.assertTrue(await setup_module.async_setup_entry(self.hass, self.entry))
            forward.assert_awaited_once()
            self.assertFalse(self.entry.runtime_data.notifications_ready)
            self.assertIsNotNone(self.entry.runtime_data.snapshot)
            self.client.close.assert_not_awaited()

    async def test_first_refresh_without_cache_retries_setup_and_closes_client(self):
        self.client.get_home_status.side_effect = client_module.DTEKApiError("offline")
        self.client.close = AsyncMock()
        with (
            patch.object(
                setup_module, "_async_migrate_registry_identifiers", AsyncMock()
            ),
            patch.object(setup_module, "create_provider", return_value=self.provider),
        ):
            with self.assertRaises(ConfigEntryNotReady):
                await setup_module.async_setup_entry(self.hass, self.entry)
            self.client.close.assert_awaited_once()

    async def test_first_refresh_without_schedule_loads_address_and_recovers(self):
        self.client.get_home_status.return_value["showTablePlan"] = False
        self.entry.add_update_listener = Mock(return_value=lambda: None)
        with (
            patch.object(
                setup_module, "_async_migrate_registry_identifiers", AsyncMock()
            ),
            patch.object(setup_module, "create_provider", return_value=self.provider),
        ):
            self.assertTrue(await setup_module.async_setup_entry(self.hass, self.entry))
        coord = self.entry.runtime_data
        self.hass.config_entries.async_forward_entry_setups.assert_awaited_once()
        self.assertTrue(coord.address_success)
        self.assertFalse(coord.notifications_ready)
        self.assertIsNone(coord.snapshot)
        self.assertEqual(coord.data["schedule_groups"], ["GPV5.1"])
        self.assertTrue(coord.last_update_success)
        self.assertEqual(coord.connection_state, "ok")
        self.assertEqual(
            sensors.DTEKScheduleStatusSensor(coord, self.entry).native_value, "missing"
        )
        self.assertTrue(binary.DTEKPowerBinarySensor(coord, self.entry).available)
        calendar_entity = calendar.DTEKScheduleCalendar(
            coord, self.entry, "outage", "outage_schedule", "outage_schedule"
        )
        self.assertFalse(calendar_entity.available)
        now = datetime.now(coordinator.KYIV_TZ)
        with self.assertRaises(calendar.HomeAssistantError):
            await calendar_entity.async_get_events(
                self.hass, now, now + timedelta(days=1)
            )
        # Adding an HA entity listener must restart polling after first-refresh failure.
        unsubscribe = coord.async_add_listener(Mock())
        try:
            self.assertIsNotNone(coord._unsub_refresh)
            self.client.get_home_status.return_value["data"]["12А"][
                "sub_type_reason"
            ] = ["GPV3.1"]
            await coord.async_refresh()
            self.assertEqual(coord.address_data["schedule_groups"], ["GPV3.1"])
            self.assertFalse(coord.notifications_ready)
            self.assertIsNone(coord.snapshot)
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            self.schedule["fact"]["data"] = {
                str(int(day.timestamp())): {
                    "GPV3.1": {str(h): "yes" for h in range(1, 25)}
                }
            }
            await coord.async_refresh()
            self.assertTrue(calendar_entity.available)
            self.assertEqual(coord.data["schedule_groups"], ["GPV3.1"])
            self.assertEqual(coord.snapshot.groups, ("GPV3.1",))
            self.assertEqual(
                sensors.DTEKScheduleStatusSensor(coord, self.entry).native_value,
                "empty",
            )
            self.schedule["fact"]["data"] = []
            await coord.async_refresh()
            self.assertFalse(calendar_entity.available)
            self.assertEqual(
                sensors.DTEKScheduleStatusSensor(coord, self.entry).native_value,
                "missing",
            )
            self.assertEqual(coord.snapshot_attributes["data_status"], "stale")
            self.assertEqual(coord.snapshot_attributes["schedule_coverage"], [])
        finally:
            unsubscribe()

    async def test_schedule_failure_keeps_current_address_but_old_display_groups(self):
        saved = await self.snapshot()
        self.client.get_home_status.return_value = {
            **self.client.get_home_status.return_value,
            "data": {"12А": {"sub_type_reason": ["GPV3.1"]}},
        }
        self.client.check_update.side_effect = client_module.DTEKApiError(
            "schedule down"
        )
        await self.coord.async_refresh()
        self.assertTrue(self.coord.address_success)
        self.assertFalse(self.coord.notifications_ready)
        self.assertEqual(self.coord.snapshot, saved)
        self.assertEqual(self.coord.address_data["schedule_groups"], ["GPV3.1"])
        entity = sensors.DTEKSensor(
            self.coord,
            self.entry,
            next(d for d in sensors.SENSOR_DESCRIPTIONS if d.key == "schedule_group"),
        )
        self.assertTrue(entity.available)
        self.assertEqual(entity.native_value, "GPV3.1")
        self.assertFalse(entity.extra_state_attributes["schedule_notifications_ready"])
        self.client.check_update.side_effect = None
        await self.coord.async_refresh()
        self.assertTrue(self.coord.notifications_ready)
        self.assertEqual(self.coord.snapshot.groups, ("GPV3.1",))
        self.assertNotEqual(self.coord.snapshot.snapshot_id, saved.snapshot_id)
        self.assertTrue(
            all("GPV3.1" in w.description for w in self.coord.snapshot.windows)
        )

    async def test_address_failure_cannot_publish_fresh_schedule_for_old_groups(self):
        saved = await self.snapshot()
        self.client.get_home_status.side_effect = client_module.DTEKApiError(
            "address down"
        )
        await self.coord.async_refresh()
        self.assertFalse(self.coord.address_success)
        self.assertFalse(self.coord.notifications_ready)
        self.assertIsNotNone(self.coord.schedule_last_success)
        self.assertEqual(self.coord.snapshot, saved)
        self.assertFalse(binary.DTEKPowerBinarySensor(self.coord, self.entry).available)

    async def test_missing_groups_does_not_reuse_old_group_as_confirmed(self):
        saved = await self.snapshot()
        self.client.get_home_status.return_value = {"data": {}}
        await self.coord.async_refresh()
        self.assertFalse(self.coord.notifications_ready)
        self.assertEqual(self.coord.snapshot, saved)

    async def test_unchanged_check_refreshes_verified_time_but_failure_does_not(self):
        saved = await self.snapshot()
        await self.coord.async_refresh()
        self.assertEqual(saved.snapshot_id, self.coord.snapshot.snapshot_id)
        self.assertGreaterEqual(self.coord.snapshot.checked_at, saved.checked_at)
        latest = self.coord.snapshot
        self.client.check_update.side_effect = client_module.DTEKApiError("down")
        await self.coord.async_refresh()
        self.assertEqual(latest, self.coord.snapshot)
        on_disk = await self.coord._snapshot_store.async_load()
        self.assertEqual(on_disk["checked_at"], latest.checked_at.isoformat())

    async def test_retry_backoff_is_bounded_and_resets(self):
        await self.snapshot()
        self.client.check_update.side_effect = client_module.DTEKApiError("down")
        delays = []
        with patch.object(coordinator.random, "uniform", return_value=1):
            for _ in range(7):
                await self.coord.async_refresh()
                delays.append(self.coord.update_interval.total_seconds())
        self.assertEqual(delays, [60, 120, 240, 480, 600, 600, 600])
        self.client.check_update.side_effect = None
        await self.coord.async_refresh()
        self.assertEqual(self.coord.update_interval.total_seconds(), 300)

    async def test_disk_failure_keeps_live_data_and_reports_storage_error(self):
        with patch.object(
            self.coord._snapshot_store,
            "async_save",
            AsyncMock(side_effect=OSError("disk full")),
        ):
            await self.snapshot()
        self.assertTrue(self.coord.notifications_ready)
        self.assertIn("disk full", self.coord.snapshot_storage_error)

    async def test_foreign_or_corrupt_cache_is_not_restored(self):
        saved = await self.snapshot()
        for value in [None, {}, {**saved.to_dict(), "provider_id": "dtek_other"}]:
            c = coordinator.DTEKDataCoordinator(self.hass, self.provider, self.entry)
            with patch.object(
                c._snapshot_store, "async_load", AsyncMock(return_value=value)
            ):
                await c.async_load_snapshot()
            self.assertIsNone(c.snapshot)
            self.assertFalse(c.notifications_ready)

    async def test_calendar_is_not_exposed_midway_through_snapshot_save(self):
        await self.snapshot()
        self.client.get_home_status.return_value = {
            **self.client.get_home_status.return_value,
            "data": {"12А": {"sub_type_reason": ["GPV3.1"]}},
        }
        active = calendar.DTEKScheduleCalendar(
            self.coord, self.entry, "outage", "outage_schedule", "outage_schedule"
        )

        async def saving(payload):
            self.assertFalse(self.coord.notifications_ready)
            self.assertFalse(active.available)
            with self.assertRaises(calendar.HomeAssistantError):
                await active.async_get_events(
                    self.hass, self.day, self.day + timedelta(days=1)
                )

        with patch.object(
            self.coord._snapshot_store, "async_save", AsyncMock(side_effect=saving)
        ):
            await self.coord.async_refresh()
        self.assertTrue(self.coord.notifications_ready)
        self.assertEqual(self.coord.data["schedule_groups"], ["GPV3.1"])

    async def test_new_group_without_schedule_is_unknown_not_an_empty_schedule(self):
        saved = await self.snapshot()
        self.client.get_home_status.return_value = {
            **self.client.get_home_status.return_value,
            "data": {"12А": {"sub_type_reason": ["GPV9.9"]}},
        }
        await self.coord.async_refresh()
        self.assertFalse(self.coord.notifications_ready)
        self.assertEqual(self.coord.snapshot, saved)
        self.assertIn("GPV9.9", self.coord.schedule_reason)
        self.assertIsNone(self.coord.schedule_error)
        self.assertEqual(self.coord.connection_state, "ok")
