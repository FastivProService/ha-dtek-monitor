"""Region selection, duplicate detection and entity naming against real HA."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import unittest
import test_ha_runtime as runtime
from component_loader import load_component_module

if runtime.HAS_HA:
    from homeassistant.data_entry_flow import AbortFlow

    flow = runtime.flow
    helper = load_component_module("helpers")
    binary = load_component_module("binary_sensor")
    contract = load_component_module("provider")


@unittest.skipUnless(runtime.HAS_HA, "Requires real Home Assistant runtime")
class RegionalFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.RuntimeTests.asyncSetUp(self)
        self.hass.config_entries = Mock()
        self.hass.config_entries.flow.async_progress_by_handler.return_value = []
        self.hass.config_entries.async_entry_for_domain_unique_id.return_value = None
        self.flow = flow.DTEKMonitorConfigFlow()
        self.flow.hass = self.hass
        self.flow.context = {"source": "user"}
        self.provider = SimpleNamespace(
            list_localities=AsyncMock(return_value=("м. Бровари",)),
            list_streets=AsyncMock(return_value=("вул. Прикладна",)),
            list_houses=AsyncMock(return_value=("10", "1", "2")),
            close=AsyncMock(),
        )

    async def complete_address(self, provider_id):
        with patch.object(
            flow, "create_provider", return_value=self.provider
        ) as factory:
            result = await self.flow.async_step_user({"provider_id": provider_id})
            self.assertEqual(result["step_id"], "connection")
            result = await self.flow.async_step_address()
            self.assertEqual(result["step_id"], "city")
            result = await self.flow.async_step_city({"city": "м. Бровари"})
            self.assertEqual(result["step_id"], "street")
            result = await self.flow.async_step_street({"street": "вул. Прикладна"})
            self.assertEqual(result["step_id"], "house")
            self.assertEqual(self.flow._available_houses, ["1", "2", "10"])
            self.assertEqual(
                [c.args for c in factory.call_args_list],
                [(provider_id,), (provider_id,)],
            )
            self.assertTrue(all(c.kwargs.get("hass") is self.hass for c in factory.call_args_list))
            self.assertEqual(self.provider.close.await_count, 2)
        result = await self.flow.async_step_house({"house_num": "1"})
        self.assertEqual(result["step_id"], "settings")
        return await self.flow.async_step_settings({"scan_interval": 120})

    async def test_full_krem_flow_uses_selected_directory_and_identity(self):
        result = await self.complete_address("dtek_krem")
        self.assertEqual(result["data"]["provider_id"], "dtek_krem")
        self.assertEqual(
            self.flow.unique_id,
            contract.Address("dtek_krem", "м. Бровари", "вул. Прикладна", "1").identity,
        )
        self.assertIn("Київські регіональні", result["title"])

    async def test_oem_keeps_legacy_unique_id_and_duplicate_detection(self):
        await self.complete_address("dtek_oem")
        self.assertEqual(self.flow.unique_id, "м. Бровари_вул. Прикладна_1")
        self.hass.config_entries.async_entry_for_domain_unique_id.return_value = (
            SimpleNamespace(source="user")
        )
        with self.assertRaises(AbortFlow) as ctx:
            await self.flow.async_step_house({"house_num": "1"})
        self.assertEqual(ctx.exception.reason, "already_configured")

    async def test_operator_screen_is_offline_and_rejects_disabled_or_unknown(self):
        with patch.object(flow, "create_provider") as factory:
            result = await self.flow.async_step_user()
            selector = next(iter(result["data_schema"].schema.values()))
            self.assertEqual(
                {o["value"] for o in selector.config["options"]},
                {"dtek_oem", "dtek_krem", "dtek_dnem", "dtek_kem"},
            )
            for provider_id in [
                "dtek_dem",
                "unknown",
                ["dtek_oem"],
            ]:
                result = await self.flow.async_step_user({"provider_id": provider_id})
                self.assertEqual(result["errors"], {"provider_id": "invalid_provider"})
            factory.assert_not_called()

    async def test_switching_operator_discards_previous_directory_and_address(self):
        self.flow._streets_data = {"м. Одеса": ["old street"]}
        self.flow._city = "м. Одеса"
        self.flow._street = "old street"
        self.flow._house = "old house"
        self.flow._available_houses = ["old house"]
        with patch.object(flow, "create_provider", return_value=self.provider):
            await self.flow.async_step_user({"provider_id": "dtek_krem"})
            self.assertEqual(self.flow._city, "")
            self.assertEqual(self.flow._available_houses, [])
            result = await self.flow.async_step_city({"city": "м. Одеса"})
        self.assertEqual(result["errors"], {"city": "invalid_city"})
        self.provider.close.assert_awaited_once()

    async def test_krem_network_failure_closes_client_and_can_retry(self):
        self.provider.list_localities.side_effect = flow.ProviderError("offline")
        with patch.object(
            flow, "create_provider", return_value=self.provider
        ) as factory:
            result = await self.flow.async_step_user({"provider_id": "dtek_krem"})
            self.assertEqual(result["step_id"], "connection")
            result = await self.flow.async_step_address()
            self.assertEqual(result["errors"], {"base": "cannot_connect"})
            self.provider.list_localities.side_effect = None
            result = await self.flow.async_step_city({})
            self.assertFalse(result["errors"])
            self.assertEqual(self.provider.close.await_count, 2)
            self.assertTrue(
                all(c.args == ("dtek_krem",) for c in factory.call_args_list)
            )

    async def test_region_entities_share_bot_prefix_and_preserve_legacy_names(self):
        self.assertIsNone(
            helper.suggested_entity_id(self.entry, "binary_sensor", "power")
        )
        before = helper.build_device_info(self.entry)
        self.entry.data["provider_id"] = "dtek_oem"
        self.assertEqual(helper.build_device_info(self.entry), before)
        self.entry.data["provider_id"] = "dtek_krem"
        entities = [binary.DTEKPowerBinarySensor(self.coord, self.entry)]
        entities += [
            runtime.sensors.DTEKSensor(self.coord, self.entry, d)
            for d in runtime.sensors.SENSOR_DESCRIPTIONS
        ]
        entities += [
            runtime.calendar.DTEKScheduleCalendar(
                self.coord, self.entry, "outage", "outage_schedule", "outage_schedule"
            ),
            runtime.calendar.DTEKScheduleCalendar(
                self.coord,
                self.entry,
                "possible",
                "possible_schedule",
                "possible_schedule",
            ),
        ]
        prefix = (
            entities[0].entity_id.removeprefix("binary_sensor.").removesuffix("_power")
        )
        ids = {e.entity_id for e in entities}
        self.assertIn(f"sensor.{prefix}_outage_status", ids)
        self.assertIn(f"sensor.{prefix}_primary_schedule_group", ids)
        self.assertIn(f"sensor.{prefix}_all_schedule_groups", ids)
        self.assertIn(f"calendar.{prefix}_outage_schedule", ids)
        self.assertIn(f"calendar.{prefix}_possible_outage_schedule", ids)
        self.entry.data["city"] = "other city"
        self.assertNotEqual(
            helper.suggested_entity_id(self.entry, "binary_sensor", "power"),
            entities[0].entity_id,
        )
        self.entry.data["provider_id"] = "dtek_dnem"
        self.assertNotEqual(
            helper.suggested_entity_id(self.entry, "binary_sensor", "power"),
            entities[0].entity_id,
        )

    async def test_full_dnem_flow_selects_dnipropetrovsk_operator(self):
        self.provider.list_localities.return_value = ("м. Дніпро",)
        self.provider.list_streets.return_value = ("пров. Прикладний",)
        with patch.object(
            flow, "create_provider", return_value=self.provider
        ) as factory:
            result = await self.flow.async_step_user({"provider_id": "dtek_dnem"})
            self.assertEqual(result["step_id"], "connection")
            result = await self.flow.async_step_address()
            self.assertEqual(result["step_id"], "city")
            await self.flow.async_step_city({"city": "м. Дніпро"})
            await self.flow.async_step_street({"street": "пров. Прикладний"})
            await self.flow.async_step_house({"house_num": "2"})
            result = await self.flow.async_step_settings({"scan_interval": 120})
        self.assertEqual(result["data"]["provider_id"], "dtek_dnem")
        self.assertEqual(
            self.flow.unique_id,
            contract.Address(
                "dtek_dnem", "м. Дніпро", "пров. Прикладний", "2"
            ).identity,
        )
        self.assertTrue(all(c.args == ("dtek_dnem",) for c in factory.call_args_list))
        self.provider.close.assert_awaited()
        self.assertIn("Дніпровські", result["title"])

    async def test_kem_skips_city_and_closes_directory_and_house_clients(self):
        self.provider.list_localities.return_value = ("м. Київ",)
        self.provider.list_streets.return_value = ("вул. Прикладна",)
        self.provider.list_houses.return_value = ("2А",)
        with patch.object(
            flow, "create_provider", return_value=self.provider
        ) as factory:
            result = await self.flow.async_step_user({"provider_id": "dtek_kem"})
            self.assertEqual(result["step_id"], "connection")
            result = await self.flow.async_step_address()
            self.assertEqual(result["step_id"], "street")
            self.assertEqual(self.flow._city, "м. Київ")
            await self.flow.async_step_street({"street": "вул. Прикладна"})
            await self.flow.async_step_house({"house_num": "2А"})
            result = await self.flow.async_step_settings({"scan_interval": 120})
        self.assertEqual(result["data"]["provider_id"], "dtek_kem")
        self.assertEqual(result["data"]["city"], "м. Київ")
        self.assertTrue(all(c.args == ("dtek_kem",) for c in factory.call_args_list))
        self.assertEqual(self.provider.close.await_count, 2)

    async def test_kem_directory_failure_can_retry_on_street_step(self):
        self.provider.list_localities.side_effect = flow.ProviderError("offline")
        with patch.object(flow, "create_provider", return_value=self.provider):
            result = await self.flow.async_step_user({"provider_id": "dtek_kem"})
            self.assertEqual(result["step_id"], "connection")
            result = await self.flow.async_step_address()
            self.assertEqual(result["step_id"], "street")
            self.assertEqual(result["errors"], {"base": "cannot_connect"})
            self.provider.list_localities.side_effect = None
            self.provider.list_localities.return_value = ("м. Київ",)
            result = await self.flow.async_step_street({})
            self.assertFalse(result["errors"])
        self.assertEqual(self.provider.close.await_count, 2)
