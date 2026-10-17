"""HA lifecycle and calendars depend only on the normalized provider contract."""

from datetime import datetime, timedelta, timezone
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import test_ha_runtime as runtime
from component_loader import load_component_module

if runtime.HAS_HA:
    coordinator = load_component_module("coordinator")
    setup = load_component_module("__init__")
    calendar = load_component_module("calendar")
    helper = load_component_module("helpers")
    contract = load_component_module("provider")


@unittest.skipUnless(runtime.HAS_HA, "Requires real Home Assistant runtime")
class ProviderRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.RuntimeTests.asyncSetUp(self)

    async def test_legacy_entry_migration_preserves_entity_ids_and_options(self):
        self.entry.version = 2
        before = helper.build_entity_unique_id(self.entry, "power")
        data = dict(self.entry.data)
        options = dict(self.entry.options)

        def update(entry, **kwargs):
            for key, value in kwargs.items():
                setattr(entry, key, value)

        self.hass.config_entries = SimpleNamespace(
            async_update_entry=Mock(side_effect=update)
        )
        self.assertTrue(await setup.async_migrate_entry(self.hass, self.entry))
        self.assertEqual(self.entry.version, 3)
        self.assertEqual(self.entry.data, {**data, "provider_id": "dtek_oem"})
        self.assertEqual(self.entry.options, options)
        self.assertEqual(helper.build_entity_unique_id(self.entry, "power"), before)
        self.assertTrue(await setup.async_migrate_entry(self.hass, self.entry))
        self.hass.config_entries.async_update_entry.assert_called_once()

    async def test_coordinator_accepts_a_provider_without_dtek_transport_methods(self):
        now = datetime.now(timezone.utc)
        start = now.astimezone(coordinator.KYIV_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        address = contract.Address("dtek_oem", *self.coord.address.parts)
        publication = contract.Publication(True, True, False, True)
        reading = contract.AddressReading(
            address,
            {
                "power_on": True,
                "outage_type": "ok",
                "schedule_groups": ["opaque-line"],
                "primary_schedule_group": "opaque-line",
            },
            ("opaque-line",),
            now,
            now,
            publication,
        )
        schedule = contract.ScheduleReading(
            address,
            (contract.Group("opaque-line", "Лінія"),),
            (),
            (
                contract.Coverage(
                    "opaque-line", "outage", start, start + timedelta(days=1)
                ),
            ),
            now,
            now,
            publication,
            "current",
        )
        provider = SimpleNamespace(
            provider_id="dtek_oem",
            refresh=AsyncMock(
                return_value=contract.RefreshResult(
                    reading, schedule, schedule_checked_at=now
                )
            ),
            diagnostics={},
            close=AsyncMock(),
        )
        c = coordinator.DTEKDataCoordinator(self.hass, provider, self.entry)
        await c.async_refresh()
        self.assertTrue(c.notifications_ready)
        self.assertEqual(c.data["schedule_groups"], ["opaque-line"])
        self.assertEqual(c.data["group_labels"], {"opaque-line": "Лінія"})
        self.assertNotIn("schedule_fact", c.data)
        self.assertIsNotNone(c.snapshot.contract_revision)
        provider.refresh.return_value = contract.RefreshResult(
            replace(reading, address=replace(address, house="other")), schedule
        )
        await c.async_refresh()
        self.assertFalse(c.notifications_ready)
        self.assertFalse(c.address_success)

    async def test_unpublished_weekly_data_cannot_fill_tomorrow_calendar(self):
        day = datetime.now(coordinator.KYIV_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        self.schedule["fact"]["data"] = {
            str(int(day.timestamp())): {"GPV5.1": {str(h): "yes" for h in range(1, 25)}}
        }
        self.client.get_home_status.return_value["showTablePlan"] = False
        await self.coord.async_refresh()
        self.assertTrue(self.coord.notifications_ready)
        entity = calendar.DTEKScheduleCalendar(
            self.coord, self.entry, "outage", "outage_schedule", "outage_schedule"
        )
        self.assertEqual(
            await entity.async_get_events(self.hass, day, day + timedelta(days=1)), []
        )
        with self.assertRaises(calendar.HomeAssistantError):
            await entity.async_get_events(
                self.hass, day + timedelta(days=1), day + timedelta(days=2)
            )

    async def test_publication_revocation_preserves_old_snapshot_only_for_display(self):
        await self.coord.async_refresh()
        saved = self.coord.snapshot
        self.client.get_home_status.return_value["showCurSchedule"] = False
        await self.coord.async_refresh()
        self.assertTrue(self.coord.address_success)
        self.assertFalse(self.coord.notifications_ready)
        self.assertEqual(self.coord.schedule_state, "unpublished")
        self.assertEqual(self.coord.snapshot, saved)
        self.assertEqual(self.coord.snapshot_attributes["schedule_coverage"], [])

    async def test_reading_from_a_different_provider_cannot_restore_legacy_cache(self):
        await self.coord.async_refresh()
        self.entry.data["provider_id"] = "dtek_krem"
        fake = SimpleNamespace(provider_id="dtek_krem")
        other = coordinator.DTEKDataCoordinator(self.hass, fake, self.entry)
        await other.async_load_snapshot()
        self.assertIsNone(other.snapshot)

    async def test_new_config_entry_has_explicit_provider_and_old_identity(self):
        f = runtime.flow.DTEKMonitorConfigFlow()
        f.hass = self.hass
        f._city = "м. Одеса"
        f._street = "вулиця Прикладна"
        f._house = "12А"
        result = await f.async_step_settings({"scan_interval": 300})
        self.assertEqual(result["data"]["provider_id"], "dtek_oem")
        self.assertEqual(result["data"]["city"], "м. Одеса")
        self.assertEqual(result["version"], 3)

    async def test_dnem_multigroup_calendar_and_cek_revocation(self):
        from test_provider_contract import fixture

        f = fixture("dnem")
        day = datetime.now(coordinator.KYIV_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        fact = f["schedule"]["fact"]
        fact["data"] = {str(int(day.timestamp())): next(iter(fact["data"].values()))}
        fact["update"] = day.strftime("%d.%m.%Y %H:%M")
        self.entry.data = {
            "provider_id": "dtek_dnem",
            "city": "м. Дніпро",
            "street": "пров. Прикладний",
            "house_num": "2",
        }
        self.client.get_home_status.return_value = f["house_response"]
        self.client.consume_schedule_update.return_value = f["schedule"]
        provider = runtime.adapter.DTEKWebProvider(
            runtime.registry.PROVIDERS["dtek_dnem"], self.client
        )
        coord = coordinator.DTEKDataCoordinator(self.hass, provider, self.entry)
        await coord.async_refresh()
        self.assertTrue(coord.notifications_ready)
        self.assertEqual(coord.data["schedule_groups"], ["GPV1.2", "GPV5.1"])
        entity = calendar.DTEKScheduleCalendar(
            coord, self.entry, "outage", "outage_schedule", "outage_schedule"
        )
        events = await entity.async_get_events(self.hass, day, day + timedelta(days=1))
        self.assertEqual(
            {event.description.split("group=")[1] for event in events},
            {"GPV1.2", "GPV5.1"},
        )
        snapshot = coord.snapshot
        f["house_response"]["data"]["2"]["cek"] = True
        await coord.async_refresh()
        self.assertEqual(coord.schedule_state, "unsupported")
        self.assertFalse(coord.notifications_ready)
        self.assertFalse(entity.available)
        self.assertEqual(coord.snapshot, snapshot)
        self.assertEqual(coord.snapshot_attributes["schedule_coverage"], [])
        with self.assertRaises(calendar.HomeAssistantError):
            await entity.async_get_events(self.hass, day, day + timedelta(days=1))

    async def test_kem_keeps_internal_provenance_and_publishes_hidden_label_flag(self):
        from test_provider_contract import fixture

        f = fixture("kem")
        day = datetime.now(coordinator.KYIV_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        fact = f["schedule"]["fact"]
        fact["data"] = {str(int(day.timestamp())): next(iter(fact["data"].values()))}
        fact["update"] = day.strftime("%d.%m.%Y %H:%M")
        self.entry.data = {
            "provider_id": "dtek_kem",
            "city": "м. Київ",
            "street": "вул. Прикладна",
            "house_num": "2А",
        }
        self.client.get_home_status.return_value = f["house_response"]
        self.client.consume_schedule_update.return_value = f["schedule"]
        provider = runtime.adapter.DTEKWebProvider(
            runtime.registry.PROVIDERS["dtek_kem"], self.client
        )
        coord = coordinator.DTEKDataCoordinator(self.hass, provider, self.entry)
        await coord.async_refresh()
        self.assertTrue(coord.notifications_ready)
        self.client.get_home_status.assert_awaited_once_with(
            "", "вул. Прикладна", "2А"
        )
        self.assertEqual(coord.data["schedule_groups"], ["GPV29.1", "GPV35.1"])
        self.assertEqual(coord.data["group_labels"], {"GPV29.1": None, "GPV35.1": None})
        sensor = runtime.sensors.DTEKSensor(
            coord,
            self.entry,
            next(
                d
                for d in runtime.sensors.SENSOR_DESCRIPTIONS
                if d.key == "schedule_group"
            ),
        )
        self.assertFalse(sensor.extra_state_attributes["group_labels_visible"])
        calendar_entity = calendar.DTEKScheduleCalendar(
            coord, self.entry, "outage", "outage_schedule", "outage_schedule"
        )
        events = await calendar_entity.async_get_events(
            self.hass, day, day + timedelta(days=1)
        )
        self.assertEqual(
            {e.description.split("group=")[1] for e in events}, {"GPV29.1", "GPV35.1"}
        )
        self.client.check_update.return_value = {"showUserGroup": True}
        await coord.async_refresh()
        self.assertTrue(sensor.extra_state_attributes["group_labels_visible"])
