"""Stable address upgrades preserve real HA registries and durable settings."""

import tempfile
import unittest
from types import MappingProxyType

from component_loader import load_component_module
from test_ha_runtime import HAS_HA, load_registries

if HAS_HA:
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    setup = load_component_module("__init__")
    helpers = load_component_module("helpers")


@unittest.skipUnless(HAS_HA, "Requires real Home Assistant")
class StableUpgradeTests(unittest.IsolatedAsyncioTestCase):
    async def test_stable_addresses_keep_bot_entity_ids_devices_and_options_after_restart(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            hass.config_entries = ConfigEntries(hass, {})
            await load_registries(hass)
            registry = er.async_get(hass)
            devices = dr.async_get(hass)
            entries = []
            before = {}
            for provider in ("dtek_oem", "dtek_krem", "dtek_dnem", "dtek_kem"):
                entry = ConfigEntry(
                    version=3,
                    minor_version=1,
                    domain="dtek_monitor",
                    title=provider,
                    data={
                        "provider_id": provider,
                        "city": "Приклад",
                        "street": "Тестова",
                        "house_num": "1",
                    },
                    options={"scan_interval": 600},
                    unique_id=f"{provider}_stable_address",
                    source="user",
                    discovery_keys=MappingProxyType({}),
                    subentries_data=None,
                )
                hass.config_entries._entries[entry.entry_id] = entry
                entries.append(entry)
                device = devices.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    identifiers={("dtek_monitor", entry.unique_id)},
                    name="User building",
                )
                for domain, suffix in (
                    ("binary_sensor", "power"),
                    ("sensor", "schedule_groups"),
                    ("calendar", "outage_schedule"),
                    ("calendar", "possible_schedule"),
                ):
                    item = registry.async_get_or_create(
                        domain,
                        "dtek_monitor",
                        helpers.build_entity_unique_id(entry, suffix),
                        config_entry=entry,
                        device_id=device.id,
                        suggested_object_id=f"custom_{provider}_{suffix}",
                    )
                    before[item.entity_id] = (
                        item.unique_id,
                        item.device_id,
                        entry.entry_id,
                    )
                old_data = dict(entry.data)
                self.assertTrue(await setup.async_migrate_entry(hass, entry))
                self.assertEqual(entry.version, 4)
                self.assertEqual(
                    dict(entry.data), {**old_data, "entry_kind": "address"}
                )
                self.assertEqual(dict(entry.options), {"scan_interval": 600})
                # Normal setup runs this helper too; it must be idempotent.
                await setup._async_migrate_registry_identifiers(hass, entry)
                self.assertTrue(await setup.async_migrate_entry(hass, entry))
            await hass.config_entries._store.async_save(
                hass.config_entries._data_to_save()
            )
            await registry._store.async_save(registry._data_to_save())
            await devices._store.async_save(devices._data_to_save())
            # Read the actual persisted HA files in a fresh HA instance.
            restored = HomeAssistant(directory)
            restored.config_entries = ConfigEntries(restored, {})
            await restored.config_entries.async_initialize()
            await load_registries(restored)
            restored_registry = er.async_get(restored)
            restored_devices = dr.async_get(restored)
            self.assertEqual(
                len(restored.config_entries.async_entries("dtek_monitor")), 4
            )
            for entry in entries:
                loaded = restored.config_entries.async_get_entry(entry.entry_id)
                self.assertEqual(loaded.version, 4)
                self.assertEqual(loaded.unique_id, entry.unique_id)
                self.assertEqual(dict(loaded.options), dict(entry.options))
            self.assertEqual(len(restored_registry.entities), len(before))
            for entity_id, expected in before.items():
                entity = restored_registry.async_get(entity_id)
                self.assertEqual(
                    (entity.unique_id, entity.device_id, entity.config_entry_id),
                    expected,
                )
                self.assertIsNotNone(restored_devices.async_get(entity.device_id))

    async def test_future_schema_is_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            hass.config_entries = ConfigEntries(hass, {})
            entry = ConfigEntry(
                version=5,
                minor_version=1,
                domain="dtek_monitor",
                title="Future",
                data={"entry_kind": "future"},
                options={"setting": True},
                unique_id="future",
                source="user",
                discovery_keys=MappingProxyType({}),
                subentries_data=None,
            )
            hass.config_entries._entries[entry.entry_id] = entry
            before = (dict(entry.data), dict(entry.options), entry.version)
            self.assertFalse(await setup.async_migrate_entry(hass, entry))
            self.assertEqual(
                (dict(entry.data), dict(entry.options), entry.version), before
            )
