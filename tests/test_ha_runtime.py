"""Behavior tests against real Home Assistant; skipped without HA installed."""

from __future__ import annotations

import importlib.util
import logging
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from component_loader import load_component_module

HAS_HA = importlib.util.find_spec("homeassistant") is not None
if HAS_HA:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import EntityPlatform
    from homeassistant.helpers import entity_registry as er
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers.update_coordinator import UpdateFailed

    coordinator = load_component_module("coordinator")
    calendar = load_component_module("calendar")
    flow = load_component_module("config_flow")
    sensors = load_component_module("sensor")
    client_module = load_component_module("dtek_client")
    adapter = load_component_module("dtek_provider")
    registry = load_component_module("providers")


@unittest.skipUnless(HAS_HA, "Requires real Home Assistant runtime")
class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hass = HomeAssistant(self.temp.name)
        self.entry = SimpleNamespace(
            data={
                "city": "м. Одеса",
                "street": "вулиця Прикладна",
                "house_num": "12А",
            },
            options={},
            unique_id="test-address",
            entry_id="test-entry",
            pref_disable_polling=False,
            async_on_unload=Mock(),
        )
        self.client = Mock(spec=client_module.DTEKClient)
        self.client.transport_errors = 0
        self.client.waf_challenges = 0
        self.client.waf_hard_blocks = 0
        self.client.impersonate_target = "chrome136"
        self.client.get_home_status = AsyncMock(
            return_value={
                "data": {"12А": {"sub_type_reason": ["GPV5.1"]}},
                "showCurSchedule": True,
                "showTableSchedule": True,
                "showTableFact": True,
                "showTablePlan": True,
                "showUserGroup": True,
            }
        )
        self.schedule = {
            "fact": {"data": {}, "update": "17.10.2026 12:00"},
            "preset": {
                "data": {
                    "GPV5.1": {
                        str(d): {str(h): "yes" for h in range(1, 25)}
                        for d in range(1, 8)
                    }
                }
            },
        }
        self.client.consume_schedule_update.return_value = self.schedule
        self.client.get_schedule_data.return_value = self.schedule
        self.client.check_update = AsyncMock(return_value=None)
        self.provider = adapter.DTEKWebProvider(
            registry.PROVIDERS["dtek_oem"], self.client
        )
        self.coord = coordinator.DTEKDataCoordinator(
            self.hass, self.provider, self.entry
        )

    async def test_failed_poll_is_unavailable_immediately_and_recovers(self):
        await self.coord.async_refresh()
        self.assertTrue(self.coord.last_update_success)
        success_time = self.coord.last_success
        seen = []
        remove = self.coord.async_add_diagnostic_listener(
            lambda: seen.append(self.coord.consecutive_failures)
        )
        self.client.get_home_status.side_effect = client_module.DTEKProtectionError(
            "blocked"
        )
        for _ in range(3):
            await self.coord.async_refresh()
            self.assertFalse(self.coord.last_update_success)
            self.assertEqual(self.coord.connection_state, "error")
            self.assertEqual(self.coord.last_success, success_time)
        self.assertEqual(seen, [1, 2, 3])
        self.client.get_home_status.side_effect = None
        await self.coord.async_refresh()
        self.assertTrue(self.coord.last_update_success)
        self.assertEqual(self.coord.connection_state, "ok")
        self.assertEqual(seen[-1], 0)
        remove()
        self.assertFalse(self.coord._diagnostic_listeners)

    async def test_schedule_check_failure_does_not_publish_partial_refresh(self):
        await self.coord.async_refresh()
        previous = self.coord.data
        self.client.consume_schedule_update.return_value = {
            "fact": {"data": {"new": {}}, "update": "17.10.2026 13:00"}
        }
        self.client.check_update.side_effect = client_module.DTEKApiError("delta down")
        await self.coord.async_refresh()
        self.assertFalse(self.coord.last_update_success)
        self.assertIs(self.coord.data, previous)
        self.assertEqual(self.provider._raw["fact"]["data"], {})
        self.assertEqual(self.coord._schedule_update_time, "17.10.2026 12:00")
        self.client.consume_schedule_update.return_value = None
        self.client.get_schedule_data.return_value = {
            "fact": {"data": {"new": {}}, "update": "17.10.2026 13:00"}
        }
        self.client.check_update.side_effect = None
        await self.coord.async_refresh()
        self.assertTrue(self.coord.last_update_success)
        self.assertEqual(self.coord._schedule_update_time, "17.10.2026 13:00")

    async def test_malformed_schedule_is_not_an_empty_calendar(self):
        await self.coord.async_refresh()
        self.client.check_update.return_value = {"fact": {"data": []}}
        await self.coord.async_refresh()
        self.assertFalse(self.coord.last_update_success)
        self.assertIn("timestamp", self.coord.last_error)

    async def test_calendar_service_rejects_cached_data_after_error(self):
        await self.coord.async_refresh()
        entity = calendar.DTEKScheduleCalendar(
            self.coord, self.entry, "outage", "outage_schedule", "outage_schedule"
        )
        self.client.get_home_status.side_effect = client_module.DTEKApiError("offline")
        await self.coord.async_refresh()
        self.assertFalse(entity.available)
        self.assertIsNone(entity.event)
        now = datetime.now(timezone.utc)
        with self.assertRaises(HomeAssistantError):
            await entity.async_get_events(self.hass, now, now)

    async def test_missing_schedule_timestamp_fails_closed(self):
        self.client.consume_schedule_update.return_value = {"fact": {"data": {}}}
        await self.coord.async_refresh()
        self.assertFalse(self.coord.last_update_success)
        self.assertIsNone(self.coord.last_success)

    async def test_diagnostic_sensor_updates_while_data_is_unavailable(self):
        entity = sensors.DTEKConnectionSensor(self.coord, self.entry)
        entity.hass = self.hass
        entity.entity_id = "sensor.dtek_test_connection"
        entity.platform = EntityPlatform(
            hass=self.hass,
            logger=logging.getLogger(__name__),
            domain="sensor",
            platform_name="dtek_monitor",
            platform=None,
            scan_interval=timedelta(seconds=30),
            entity_namespace=None,
        )
        await er.async_load(self.hass)
        entity.add_to_platform_start(self.hass, entity.platform, None)
        await entity.add_to_platform_finish()
        try:
            self.client.get_home_status.side_effect = client_module.DTEKApiError(
                "offline"
            )
            for count in range(1, 4):
                await self.coord.async_refresh()
                state = self.hass.states.get(entity.entity_id)
                self.assertEqual(state.state, "error")
                self.assertEqual(state.attributes["consecutive_failures"], count)
            self.client.get_home_status.side_effect = None
            await self.coord.async_refresh()
            self.assertEqual(self.hass.states.get(entity.entity_id).state, "ok")
        finally:
            await entity.async_remove()
        self.assertFalse(self.coord._diagnostic_listeners)

    async def test_flow_closes_client_on_success_and_error(self):
        for error in (None, client_module.DTEKApiError("offline")):
            with self.subTest(error=error):
                c = Mock()
                c.list_localities = AsyncMock(
                    return_value=("м. Одеса",), side_effect=error
                )
                c.list_streets = AsyncMock(return_value=("street",))
                c.close = AsyncMock()
                f = flow.DTEKMonitorConfigFlow()
                with patch.object(flow, "create_provider", return_value=c):
                    if error:
                        with self.assertRaises(client_module.DTEKApiError):
                            await f._fetch_streets()
                    else:
                        await f._fetch_streets()
                        await f._fetch_streets()
                        c.list_localities.assert_awaited_once()
                c.close.assert_awaited_once()

    async def test_submitted_city_network_failure_returns_form(self):
        f = flow.DTEKMonitorConfigFlow()
        f.hass = self.hass
        f._fetch_streets = AsyncMock(side_effect=client_module.DTEKApiError("offline"))
        result = await f.async_step_city({"city": "м. Одеса"})
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
