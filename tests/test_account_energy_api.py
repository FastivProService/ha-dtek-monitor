"""Energy source authorization, real HA state normalization and event tracking."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_account_submission_api as access
from component_loader import load_component_module
from test_account_submission import MemoryStore, context

HAS_HA = access.runtime.runtime.HAS_HA
if HAS_HA:
    api = load_component_module("account_energy_api")
    from homeassistant.core import State


@unittest.skipUnless(HAS_HA, "Requires Home Assistant")
class EnergyAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_schema_accepts_modern_zones_but_not_unknown_scales(self):
        import voluptuous as vol
        schema = api.websocket_energy._ws_schema
        msg = {**self.msg, "action": "preview", "config": {
            "mode": "offset", "entities": {"DAY_2_ZONE": "sensor.day", "NIGHT_2_ZONE": "sensor.night"},
            "max_age": 300, "max_power_kw": "20",
        }, "references": {"DAY_2_ZONE": "1000", "NIGHT_2_ZONE": "500"}}
        self.assertEqual(schema(msg)["references"], msg["references"])
        with self.assertRaises(vol.Invalid):
            schema({**msg, "references": {"UNKNOWN_SCALE": "1"}})

    async def asyncSetUp(self):
        await access.SubmissionAccessTests.asyncSetUp(self)
        self.manager = SimpleNamespace(
            **{
                action: AsyncMock(return_value={"binding": None})
                for action in ("get", "preview", "save", "remove")
            }
        )
        self.account_coord.energy = self.manager
        self.account_coord.client.read_submission_context = AsyncMock(
            return_value=context()
        )
        self.msg = {
            "id": 1,
            "type": "dtek_monitor/account_energy",
            "entity_id": "sensor.example",
            "action": "get",
        }

    async def invoke(self):
        with (
            patch.object(api.er, "async_get", return_value=self.registry),
            patch.object(
                api.er, "async_entries_for_config_entry", return_value=[self.entity]
            ),
        ):
            await api.websocket_energy.__wrapped__(self.hass, self.connection, self.msg)

    async def test_non_admin_cannot_read_or_mutate_energy_binding(self):
        self.connection.user.is_admin = False
        for action in ("get", "preview", "save", "remove"):
            self.msg["action"] = action
            await self.invoke()
            getattr(self.manager, action).assert_not_awaited()
        self.assertEqual(self.connection.send_error.call_args.args[1], "unauthorized")

    async def test_source_read_permission_is_required_in_addition_to_account_access(
        self,
    ):
        self.connection.user.permissions.check_entity.side_effect = (
            lambda entity, policy: entity != "sensor.grid"
        )

        async def get(number, user, authorize):
            authorize("sensor.grid")

        self.manager.get.side_effect = get
        await self.invoke()
        self.assertEqual(self.connection.send_error.call_args.args[1], "unauthorized")
        self.connection.send_result.assert_not_called()

    async def test_calibration_is_local_even_when_dtek_is_unavailable(self):
        self.msg.update(
            action="preview", revision="old", references={"GENERAL": "1030"}
        )
        self.account_coord.client.read_submission_context.side_effect = (
            api.AccountError("offline")
        )
        await self.invoke()
        self.account_coord.client.read_submission_context.assert_not_awaited()
        self.assertEqual(
            self.manager.preview.call_args.args[:7],
            (
                "100001",
                "admin-user",
                None,
                None,
                {"GENERAL": "1030"},
                "old",
                self.manager.preview.call_args.args[6],
            ),
        )
        self.connection.send_result.assert_called_once()

    async def test_new_binding_checks_live_meter_and_revocation_before_local_save(self):
        self.msg.update(
            action="preview",
            config={
                "mode": "direct",
                "entities": {"GENERAL": "sensor.grid"},
                "max_age": 300,
                "max_power_kw": "20",
            },
        )
        await self.invoke()
        self.account_coord.client.read_submission_context.assert_awaited_once_with(
            "100001"
        )
        self.assertEqual(self.manager.preview.call_args.args[2]["serial"], "TEST-METER")
        self.connection.send_result.reset_mock()

        async def save(number, user, token, authorize, **kwargs):
            self.connection.user.is_active = False
            authorize("sensor.grid")

        self.manager.save.side_effect = save
        self.msg.update(action="save", confirmation="token")
        await self.invoke()
        self.connection.send_result.assert_not_called()


@unittest.skipUnless(HAS_HA, "Requires Home Assistant")
class EnergySourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await access.runtime.AccountRuntimeTests.asyncSetUp(self)
        self.registry = api.er.async_get(self.hass)
        self.source = self.registry.async_get_or_create(
            "sensor", "test_source", "grid-meter"
        )
        self.entity_id = self.source.entity_id
        self.attributes = {
            "device_class": "energy",
            "state_class": "total_increasing",
            "unit_of_measurement": "kWh",
        }

    def state(self, value="100", **attrs):
        return State(self.entity_id, value, {**self.attributes, **attrs})

    async def test_wh_conversion_and_unchanged_energy_observation_time(self):
        state = self.state("123456.000000", unit_of_measurement="Wh")
        result = api.source_snapshot(self.hass, self.entity_id, state)
        self.assertEqual(result["value"], "123.456")
        tiny = api.source_snapshot(
            self.hass, self.entity_id, self.state("0.000001", unit_of_measurement="Wh")
        )
        energy = load_component_module("account_energy")
        self.assertEqual(str(energy.amount(tiny["value"])), "1E-9")
        self.assertEqual(result["at"], state.last_reported.isoformat())
        old = datetime.now(timezone.utc) - timedelta(days=1)
        state = State(self.entity_id, "100", self.attributes, last_changed=old)
        self.assertNotEqual(
            api.source_snapshot(self.hass, self.entity_id, state)["at"], old.isoformat()
        )

    async def test_identical_real_ha_state_report_is_fresh_without_value_change(self):
        self.hass.states.async_set(self.entity_id, "100", self.attributes)
        state = self.hass.states.get(self.entity_id)
        old = datetime.now(timezone.utc) - timedelta(days=30)
        state.last_changed = state.last_updated = state.last_reported = old
        self.hass.states.async_set(self.entity_id, "100", self.attributes)
        current = self.hass.states.get(self.entity_id)
        self.assertEqual(current.last_updated, old)
        self.assertGreater(current.last_reported, old)
        self.assertEqual(
            api.source_snapshot(self.hass, self.entity_id)["at"],
            current.last_reported.isoformat(),
        )

    async def test_invalid_unavailable_negative_and_non_energy_sources_rejected(self):
        for value in (
            "unavailable",
            "unknown",
            "NaN",
            "Infinity",
            "-1",
            "1e99999999",
            "1e-99999999",
        ):
            with self.assertRaisesRegex(api.SubmissionError, "source_unavailable"):
                api.source_snapshot(self.hass, self.entity_id, self.state(value))
        with self.assertRaisesRegex(api.SubmissionError, "source_unavailable"):
            api.source_snapshot(self.hass, self.entity_id, self.state(restored=True))
        for attrs in (
            {"unit_of_measurement": "W"},
            {"state_class": "measurement"},
            {"device_class": "power"},
        ):
            with self.assertRaisesRegex(api.SubmissionError, "invalid_energy_source"):
                api.source_snapshot(self.hass, self.entity_id, self.state(**attrs))
        with self.assertRaises(api.SubmissionError):
            api.source_snapshot(self.hass, "sensor.unregistered", self.state())
        own = self.registry.async_get_or_create(
            "sensor", "dtek_monitor", "billing-reading"
        )
        with self.assertRaises(api.SubmissionError):
            api.source_snapshot(
                self.hass, own.entity_id, State(own.entity_id, "100", self.attributes)
            )

    async def test_real_state_events_latch_transient_reset_without_open_card(self):
        manager = api.HAEnergyManager(self.hass, self.entry)
        manager.store = MemoryStore()
        self.hass.states.async_set(self.entity_id, "100", self.attributes)
        await manager.start()
        draft = await manager.preview(
            "100001",
            "user",
            context(),
            {
                "mode": "offset",
                "entities": {"GENERAL": self.entity_id},
                "max_age": 900,
                "max_power_kw": "30",
            },
            {"GENERAL": "1000"},
            None,
            Mock(),
        )
        await manager.save("100001", "user", draft["confirmation"], Mock())
        self.hass.states.async_set(self.entity_id, "0", self.attributes)
        self.hass.states.async_set(self.entity_id, "100.1", self.attributes)
        await self.hass.async_block_till_done()
        view = await manager.get("100001", "user", Mock())
        self.assertEqual(view["readings"]["GENERAL"]["error"], "source_reset")
        await manager.close()
        self.assertIsNone(manager._unsubscribe)
