"""History access must remain scoped to the permitted, selected account."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import test_account_runtime as runtime
from component_loader import load_component_module

if runtime.runtime.HAS_HA:
    api = load_component_module("account_history_api")
    from homeassistant.config_entries import ConfigEntryState


@unittest.skipUnless(runtime.runtime.HAS_HA, "Requires Home Assistant")
class HistoryAccessTests(unittest.IsolatedAsyncioTestCase):
    async def identity(self, reveal):
        with patch.object(
            api.er, "async_get", return_value=self.registry
        ), patch.object(
            api.er,
            "async_entries_for_config_entry",
            return_value=[self.entity, *getattr(self, "extra_entities", [])],
        ):
            api.websocket_identity(
                self.hass,
                self.connection,
                {
                    "id": 2,
                    "entity_id": self.entity.entity_id,
                    "reveal": reveal,
                },
            )

    async def test_identity_defaults_masked_and_read_permission_allows_explicit_reveal(
        self,
    ):
        self.connection.user.is_admin = False
        await self.identity(False)
        self.connection.send_result.assert_called_with(2, {"masked": "••••0001"})
        await self.identity(True)
        self.connection.send_result.assert_called_with(
            2, {"masked": "••••0001", "number": "100001"}
        )
        self.account_coord.history.get.assert_not_awaited()

    async def test_identity_reveal_rechecks_permissions_and_account_membership(self):
        await self.identity(True)
        self.connection.send_result.reset_mock()
        self.connection.user.permissions.check_entity.return_value = False
        await self.identity(True)
        self.connection.send_result.assert_not_called()
        self.assertEqual(self.connection.send_error.call_args.args[1], "unauthorized")
        self.connection.user.permissions.check_entity.return_value = True
        self.entry.data = {**self.entry.data, "accounts": {}}
        await self.identity(True)
        self.connection.send_result.assert_not_called()
        self.assertEqual(self.connection.send_error.call_args.args[1], "not_available")

    async def asyncSetUp(self):
        await runtime.AccountRuntimeTests.asyncSetUp(self)
        self.entry.state = ConfigEntryState.LOADED
        self.entry.domain = "dtek_monitor"
        self.entry.runtime_data = self.account_coord
        self.account_coord.history = SimpleNamespace(
            get=AsyncMock(return_value={"year": 2026, "readings": []})
        )
        self.hass.config_entries.async_get_entry.return_value = self.entry
        self.entity = SimpleNamespace(
            entity_id="sensor.example",
            config_entry_id=self.entry.entry_id,
            platform="dtek_monitor",
            disabled_by=None,
            unique_id=f"account:{runtime.account.account_key('dtek_oem','100001')}:account_updated:all",
        )
        self.registry = SimpleNamespace(async_get=Mock(return_value=self.entity))
        self.connection = Mock()
        self.connection.user.permissions.check_entity.return_value = True
        self.msg = {
            "id": 1,
            "type": "dtek_monitor/account_history",
            "entity_id": "sensor.example",
            "year": 2026,
            "force": False,
        }

    async def invoke(self):
        with patch.object(
            api.er, "async_get", return_value=self.registry
        ), patch.object(
            api.er,
            "async_entries_for_config_entry",
            return_value=[self.entity, *getattr(self, "extra_entities", [])],
        ):
            await api.websocket_history.__wrapped__(
                self.hass, self.connection, self.msg
            )

    async def test_timestamp_permission_cannot_bypass_restricted_consumption(self):
        self.extra_entities = [
            SimpleNamespace(
                entity_id="sensor.private_consumption",
                platform="dtek_monitor",
                unique_id=self.entity.unique_id.replace(
                    "account_updated", "account_consumption"
                ),
            )
        ]
        self.connection.user.permissions.check_entity.side_effect = (
            lambda entity_id, policy: entity_id != "sensor.private_consumption"
        )
        await self.invoke()
        self.account_coord.history.get.assert_not_awaited()
        self.assertEqual(self.connection.send_error.call_args.args[1], "unauthorized")

    async def test_permitted_entity_resolves_one_selected_account_only(self):
        await self.invoke()
        self.account_coord.history.get.assert_awaited_once_with(
            "100001", 2026, force=False
        )
        self.connection.send_result.assert_called_once()
        self.assertNotIn("100001", str(self.connection.send_result.call_args))

    async def test_permission_denial_never_reads_registry_or_cache(self):
        self.connection.user.permissions.check_entity.return_value = False
        await self.invoke()
        self.registry.async_get.assert_not_called()
        self.account_coord.history.get.assert_not_awaited()
        self.assertEqual(self.connection.send_error.call_args.args[1], "unauthorized")

    async def test_public_entity_foreign_account_disabled_entity_or_unloaded_entry_is_denied(
        self,
    ):
        cases = [
            ("unique_id", "public-address:power"),
            ("unique_id", "account:foreign:account_updated:all"),
            ("platform", "other"),
            ("disabled_by", "user"),
        ]
        for key, value in cases:
            old = getattr(self.entity, key)
            setattr(self.entity, key, value)
            await self.invoke()
            self.account_coord.history.get.assert_not_awaited()
            setattr(self.entity, key, old)
        self.entry.state = ConfigEntryState.NOT_LOADED
        await self.invoke()
        self.account_coord.history.get.assert_not_awaited()
        self.entry.state = ConfigEntryState.LOADED
        self.entry.data = {**self.entry.data, "entry_kind": "address"}
        await self.invoke()
        self.account_coord.history.get.assert_not_awaited()

    async def test_removal_or_permission_change_during_fetch_discards_result(self):
        async def revoke(*args, **kwargs):
            self.entry.data = {**self.entry.data, "accounts": {}}
            return {"readings": ["private"]}

        self.account_coord.history.get.side_effect = revoke
        await self.invoke()
        self.connection.send_result.assert_not_called()
        self.assertEqual(self.connection.send_error.call_args.args[1], "not_available")

    async def test_auth_and_upstream_errors_are_sanitized(self):
        for error, code in [
            (runtime.account.AccountAuthError("private token"), "auth_required"),
            (runtime.account.AccountRateLimitError("private token"), "rate_limited"),
            (runtime.account.AccountError("private token"), "history_unavailable"),
        ]:
            self.account_coord.history.get.side_effect = error
            await self.invoke()
            self.assertEqual(self.connection.send_error.call_args.args[1], code)
            self.assertNotIn("private token", str(self.connection.send_error.call_args))
        self.entry.async_start_reauth.assert_called_once_with(self.hass)
