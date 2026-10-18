"""Submission permissions are stronger than read-only history permissions."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_account_runtime as runtime
from component_loader import load_component_module

if runtime.runtime.HAS_HA:
    api = load_component_module("account_submission_api")
    from homeassistant.config_entries import ConfigEntryState


@unittest.skipUnless(runtime.runtime.HAS_HA, "Requires Home Assistant")
class SubmissionAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.AccountRuntimeTests.asyncSetUp(self)
        self.entry.state = ConfigEntryState.LOADED
        self.entry.domain = "dtek_monitor"
        self.entry.runtime_data = self.account_coord
        self.manager = SimpleNamespace(
            **{
                name: AsyncMock(return_value={"status": "accepted"})
                for name in ("context", "prepare", "confirm", "acknowledge")
            }
        )
        self.account_coord.submissions = self.manager
        self.account_coord.history = SimpleNamespace(invalidate=Mock())
        self.hass.config_entries.async_get_entry.return_value = self.entry
        self.entity = SimpleNamespace(
            entity_id="sensor.example",
            config_entry_id=self.entry.entry_id,
            platform="dtek_monitor",
            disabled_by=None,
            unique_id=f"account:{runtime.account.account_key('dtek_oem', '100001')}:account_updated:all",
        )
        self.registry = SimpleNamespace(async_get=Mock(return_value=self.entity))
        self.connection = Mock()
        self.connection.user.id = "admin-user"
        self.connection.user.is_admin = True
        self.connection.user.is_active = True
        self.connection.user.permissions.check_entity.return_value = True
        self.msg = {
            "id": 1,
            "type": "dtek_monitor/account_submission",
            "entity_id": "sensor.example",
            "action": "confirm",
            "confirmation": "draft",
        }

    async def invoke(self):
        with patch.object(
            api.er, "async_get", return_value=self.registry
        ), patch.object(
            api.er, "async_entries_for_config_entry", return_value=[self.entity]
        ):
            await api.websocket_submission.__wrapped__(
                self.hass, self.connection, self.msg
            )

    async def test_admin_required_even_with_entity_read_and_control_permissions(self):
        for flag in ("is_admin", "is_active"):
            setattr(self.connection.user, flag, False)
            await self.invoke()
            self.manager.confirm.assert_not_awaited()
            self.assertEqual(
                self.connection.send_error.call_args.args[1], "unauthorized"
            )
            setattr(self.connection.user, flag, True)

    async def test_read_only_control_denial_and_foreign_entity_cannot_send(self):
        self.connection.user.permissions.check_entity.side_effect = (
            lambda entity, policy: policy != "control"
        )
        await self.invoke()
        self.manager.confirm.assert_not_awaited()
        self.connection.user.permissions.check_entity.side_effect = None
        self.entity.unique_id = "account:foreign:account_updated:all"
        await self.invoke()
        self.manager.confirm.assert_not_awaited()

    async def test_confirm_bound_to_resolved_account_user_and_draft(self):
        await self.invoke()
        args = self.manager.confirm.call_args.args
        self.assertEqual(args[:3], ("100001", "admin-user", "draft"))
        self.assertTrue(callable(args[3]))
        self.account_coord.history.invalidate.assert_called_once_with("100001")
        self.connection.send_result.assert_called_once_with(1, {"status": "accepted"})

    async def test_permission_revoked_inside_transport_guard_is_rejected(self):
        async def operation(number, user, token, authorize):
            self.connection.user.is_admin = False
            authorize()

        self.manager.confirm.side_effect = operation
        await self.invoke()
        self.connection.send_result.assert_not_called()
        self.assertEqual(self.connection.send_error.call_args.args[1], "unauthorized")

    async def test_reload_or_account_deselection_during_fetch_discards_data(self):
        self.msg["action"] = "context"

        async def operation(*args):
            self.entry.data = {**self.entry.data, "accounts": {}}
            return {"serial": "PRIVATE"}

        self.manager.context.side_effect = operation
        await self.invoke()
        self.connection.send_result.assert_not_called()

    async def test_auth_and_validation_errors_do_not_expose_upstream_details(self):
        for error, code in [
            (runtime.account.AccountAuthError("secret"), "auth_required"),
            (runtime.account.AccountError("secret"), "unavailable"),
            (api.SubmissionError("context_changed"), "context_changed"),
        ]:
            self.manager.confirm.side_effect = error
            await self.invoke()
            self.assertEqual(self.connection.send_error.call_args.args[1], code)
            self.assertNotIn("secret", str(self.connection.send_error.call_args))
