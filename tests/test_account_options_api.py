"""Dashboard shortcuts must enter the right native flow without saving anything."""

import unittest
from unittest.mock import AsyncMock, patch

import test_account_history_api as history
from component_loader import load_component_module

HAS_HA = history.runtime.runtime.HAS_HA
if HAS_HA:
    api = load_component_module("account_options_api")
    submission_api = load_component_module("account_submission_api")
    flow_module = load_component_module("config_flow")
    from homeassistant.config_entries import OptionsFlowManager


@unittest.skipUnless(HAS_HA, "Requires Home Assistant")
class AccountOptionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await history.HistoryAccessTests.asyncSetUp(self)
        self.connection.user.is_admin = True
        self.connection.user.is_active = True
        self.hass.config_entries.options.async_init = AsyncMock(
            return_value={"flow_id": "demo-flow"}
        )

    async def invoke(self, action="reminders"):
        with (
            patch.object(submission_api.er, "async_get", return_value=self.registry),
            patch.object(
                submission_api.er,
                "async_entries_for_config_entry",
                return_value=[self.entity],
            ),
        ):
            await api.websocket_options.__wrapped__(
                self.hass,
                self.connection,
                {"id": 1, "entity_id": self.entity.entity_id, "action": action},
            )

    async def test_account_identity_is_resolved_server_side_and_not_exposed(self):
        await self.invoke()
        self.hass.config_entries.options.async_init.assert_awaited_once_with(
            self.entry.entry_id,
            context={api.CONTEXT_KEY: {"account": "100001", "action": "reminders"}},
        )
        self.connection.send_result.assert_called_once_with(
            1, {"flow_id": "demo-flow", "entry_id": self.entry.entry_id}
        )
        self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_read_only_disabled_and_foreign_entities_cannot_open_forms(self):
        cases = [
            (self.connection.user, "is_admin", False),
            (self.connection.user, "is_active", False),
            (self.entity, "disabled_by", "user"),
            (self.entity, "platform", "other"),
            (self.entity, "unique_id", "account:foreign:account_updated:all"),
        ]
        for obj, key, value in cases:
            with self.subTest(key=key):
                old = getattr(obj, key)
                setattr(obj, key, value)
                await self.invoke()
                setattr(obj, key, old)
                self.hass.config_entries.options.async_init.assert_not_awaited()
        self.connection.user.permissions.check_entity.return_value = False
        await self.invoke()
        self.hass.config_entries.options.async_init.assert_not_awaited()

    async def test_lost_access_during_open_aborts_the_new_flow(self):
        async def revoke(*args, **kwargs):
            self.connection.user.is_admin = False
            return {"flow_id": "new-flow"}

        self.hass.config_entries.options.async_init.side_effect = revoke
        await self.invoke()
        self.hass.config_entries.options.async_abort.assert_called_once_with("new-flow")
        self.connection.send_result.assert_not_called()

    async def test_native_flow_skips_account_picker_for_each_scoped_action(self):
        expected = {
            "group": ("account_schedule_address", "_schedule_account"),
            "reminders": ("account_reminder_settings", "_reminder_account"),
            "notices": ("account_notice_settings", "_notice_account"),
            "checks": ("account_check_settings", "_check_account"),
        }
        self.hass.config_entries.async_entries.return_value = []
        for number in self.entry.data["accounts"]:
            for action, (step, attribute) in expected.items():
                with self.subTest(number=number, action=action):
                    manager = OptionsFlowManager(self.hass)
                    flow = flow_module.DTEKMonitorOptionsFlow(self.entry)
                    with patch.object(
                        manager, "async_create_flow", AsyncMock(return_value=flow)
                    ):
                        result = await manager.async_init(
                            self.entry.entry_id,
                            context={
                                api.CONTEXT_KEY: {"account": number, "action": action}
                            },
                        )
                        self.assertEqual(result["step_id"], step)
                        self.assertEqual(getattr(flow, attribute), number)
                        self.hass.config_entries.async_update_entry.assert_not_called()
                        manager.async_abort(result["flow_id"])

    async def test_cabinet_actions_and_removed_accounts(self):
        flow = flow_module.DTEKMonitorOptionsFlow(self.entry)
        flow.hass = self.hass
        result = await api.open_workspace_step(
            flow, self.entry, {"account": "100001", "action": "accounts"}
        )
        self.assertEqual(result["step_id"], "account_management")
        for account, action in [
            ("removed", "reminders"),
            ("100001", "account_unlink_confirm"),
        ]:
            result = await api.open_workspace_step(
                flow, self.entry, {"account": account, "action": action}
            )
            self.assertEqual(result["type"], "abort")
        self.hass.config_entries.async_update_entry.assert_not_called()
