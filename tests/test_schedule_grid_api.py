"""Read access to a status alone cannot unlock calendars or another account."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import test_ha_runtime as runtime
from component_loader import load_component_module
from test_schedule_grid import overview_fixture

if runtime.HAS_HA:
    api = load_component_module("schedule_grid_api")
    from homeassistant.config_entries import ConfigEntryState


@unittest.skipUnless(runtime.HAS_HA, "Requires Home Assistant")
class GridAPITests(unittest.TestCase):
    def setUp(self):
        c, _, _, _ = overview_fixture()
        self.entry = SimpleNamespace(
            domain="dtek_monitor",
            entry_id="entry",
            unique_id="building",
            state=ConfigEntryState.LOADED,
            data={},
            runtime_data=c,
        )
        self.entity = SimpleNamespace(
            entity_id="sensor.status",
            config_entry_id="entry",
            platform="dtek_monitor",
            disabled_by=None,
            unique_id="building_schedule_status",
        )
        self.entities = [
            self.entity,
            *[
                SimpleNamespace(
                    entity_id="calendar." + kind,
                    platform="dtek_monitor",
                    disabled_by=None,
                    unique_id="building_" + kind + "_schedule",
                )
                for kind in ("outage", "possible")
            ],
        ]
        self.hass = SimpleNamespace(
            config_entries=SimpleNamespace(
                async_get_entry=Mock(return_value=self.entry)
            )
        )
        self.connection = Mock()
        self.connection.user.permissions.check_entity.return_value = True

    def invoke(self):
        with (
            patch.object(
                api.er,
                "async_get",
                return_value=SimpleNamespace(async_get=Mock(return_value=self.entity)),
            ),
            patch.object(
                api.er, "async_entries_for_config_entry", return_value=self.entities
            ),
        ):
            api.websocket_grid(
                self.hass, self.connection, {"id": 1, "entity_id": "sensor.status"}
            )

    def test_requires_both_calendars_and_loaded_matching_integration(self):
        self.invoke()
        self.assertEqual(
            self.connection.send_result.call_args.args[1]["status"], "ready"
        )
        self.connection.reset_mock()
        self.connection.user.permissions.check_entity.side_effect = (
            lambda entity, policy: entity != "calendar.possible"
        )
        self.invoke()
        self.assertEqual(self.connection.send_error.call_args.args[1], "unauthorized")
        self.connection.send_result.assert_not_called()
        self.connection.user.permissions.check_entity.side_effect = None
        self.entity.platform = "another"
        self.invoke()
        self.assertEqual(self.connection.send_error.call_args.args[1], "not_available")

    def test_personal_selection_highlights_without_filtering_other_groups(self):
        self.entry.data = {"entry_kind": "account"}
        self.entity.unique_id = "account:hash:account_schedule_status:all"
        for e, key in zip(self.entities[1:], ("outage", "possible")):
            e.unique_id = "account:hash:account_" + key + "_schedule:all"
        with (
            patch.object(
                api,
                "resolve_readable_account",
                return_value=(self.entry, "fictional", self.entity),
            ),
            patch.object(
                api,
                "selection",
                return_value=(None, self.entry.runtime_data, ["GPV5.2"], "selected"),
            ),
        ):
            self.invoke()
        rows = self.connection.send_result.call_args.args[1]["days"][0]["rows"]
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["id"] for r in rows if r["selected"]], ["GPV5.2"])


@unittest.skipUnless(runtime.HAS_HA, "Requires Home Assistant")
class GridRefreshAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from unittest.mock import AsyncMock
        GridAPITests.setUp(self)
        self.entry.runtime_data.async_manual_refresh = AsyncMock()
        self.entry.runtime_data.schedule = overview_fixture()[1]
        self.entry.runtime_data.schedule_state = "current"
        self.entry.runtime_data.notifications_ready = True

    async def invoke(self):
        with (
            patch.object(api.er, "async_get", return_value=SimpleNamespace(async_get=Mock(return_value=self.entity))),
            patch.object(api.er, "async_entries_for_config_entry", return_value=self.entities),
        ):
            await api.websocket_refresh.__wrapped__(self.hass, self.connection, {"id": 1, "entity_id": "sensor.status"})

    async def test_read_only_user_cannot_trigger_network_refresh(self):
        self.connection.user.permissions.check_entity.side_effect = lambda entity, policy: policy == api.POLICY_READ
        await self.invoke()
        self.assertEqual(self.connection.send_error.call_args.args[1], "unauthorized")
        self.entry.runtime_data.async_manual_refresh.assert_not_awaited()

    async def test_success_still_requires_both_calendars(self):
        await self.invoke()
        self.entry.runtime_data.async_manual_refresh.assert_awaited_once()
        self.connection.send_result.assert_called_once()
        self.connection.reset_mock()
        self.connection.user.permissions.check_entity.side_effect = lambda entity, policy: entity != "calendar.possible"
        await self.invoke()
        self.assertEqual(self.connection.send_error.call_args.args[1], "unauthorized")
        self.assertEqual(self.entry.runtime_data.async_manual_refresh.await_count, 1)

    async def test_shared_cooldown_returns_safe_translated_seconds(self):
        from homeassistant.exceptions import ServiceValidationError
        self.entry.runtime_data.async_manual_refresh.side_effect = ServiceValidationError(
            translation_domain="dtek_monitor", translation_key="schedule_refresh_cooldown",
            translation_placeholders={"seconds": "120"},
        )
        await self.invoke()
        self.assertEqual(self.connection.send_error.call_args.args[1], "schedule_refresh_cooldown")
        self.assertEqual(self.connection.send_error.call_args.kwargs["translation_placeholders"], {"seconds": "120"})
        self.connection.send_result.assert_not_called()

    async def test_changed_binding_during_request_cannot_return_old_result(self):
        original = self.entry.runtime_data
        with patch.object(api, "resolve_grid", side_effect=[(original, (), None), (object(), (), None)]):
            await self.invoke()
        self.connection.send_result.assert_not_called()
        self.assertEqual(self.connection.send_error.call_args.args[1], "not_available")
