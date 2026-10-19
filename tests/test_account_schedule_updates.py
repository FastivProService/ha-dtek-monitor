"""Automatic private reassignment keeps explicit valid choices and all house groups."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_account_schedule as schedules
import test_ha_runtime as runtime
from component_loader import load_component_module

if runtime.HAS_HA:
    mod = load_component_module("account_schedule_updates")
    groups = load_component_module("account_groups")


@unittest.skipUnless(runtime.HAS_HA, "Requires real Home Assistant")
class AccountScheduleUpdatesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await schedules.AccountScheduleTests.asyncSetUp(self)
        schedules.AccountScheduleTests.link(self, [])
        self.result = groups.GroupResult("current", ("GPV3.1",), datetime.now(timezone.utc))
        self.account = NS(groups=NS(get=lambda n: self.result), async_add_listener=Mock(return_value=Mock()))
        self.personal.runtime_data = self.account
        self.manager = mod.AccountScheduleUpdates(self.hass, self.personal, self.account)
        self.addAsyncCleanup(self.manager.close)
        self.create = patch.object(mod.pn, "async_create", wraps=mod.pn.async_create).start()
        self.addCleanup(patch.stopall)
        self.hass.config_entries.async_update_entry.side_effect = lambda entry, options: setattr(entry, "options", options)
        await self.manager.reconcile()
        self.create.assert_not_called()

    async def test_cabinet_assignment_updates_calendar_and_notifies_once_across_restart(self):
        public = deepcopy(self.coord.address_data)
        self.result = groups.GroupResult("current", ("GPV5.1",), datetime.now(timezone.utc))
        await self.manager.reconcile()
        self.assertEqual(self.sensor.native_value, "GPV5.1")
        self.assertEqual(self.create.call_count, 1)
        self.assertIn("GPV3.1 → GPV5.1", self.create.call_args.args[1])
        self.assertEqual(self.coord.address_data, public)
        await self.manager.reconcile()
        restored = mod.AccountScheduleUpdates(self.hass, self.personal, self.account)
        self.addAsyncCleanup(restored.close)
        await restored.start()
        await restored._task
        self.assertEqual(self.create.call_count, 1)
        self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_invalid_manual_choice_moves_to_only_verified_group_and_stays_automatic(self):
        schedules.AccountScheduleTests.link(self, ["GPV3.1"])
        await self.manager.reconcile()
        self.coord.address_data["schedule_groups"] = ["GPV5.1"]
        self.result = groups.GroupResult("unavailable")
        await self.manager.reconcile()
        self.assertEqual(self.sensor.native_value, "GPV5.1")
        self.assertEqual(self.sensor.extra_state_attributes["selection_source"], "building_single_group")
        self.assertEqual(self.personal.options[mod.CONF_ACCOUNT_SCHEDULES]["100001"]["groups"], [])
        self.assertEqual(self.personal.options["unrelated"], 123)
        self.assertIn("GPV3.1 → GPV5.1", self.create.call_args.args[1])
        self.coord.address_data["schedule_groups"] = ["GPV5.1", "GPV3.1"]
        await self.manager.reconcile()
        self.assertIsNone(self.sensor.native_value)  # No hidden return to an obsolete manual choice.
        self.assertEqual(self.create.call_count, 1)

    async def test_invalid_manual_choice_can_use_fresh_cabinet_assignment_in_multigroup_house(self):
        schedules.AccountScheduleTests.link(self, ["GPV2.1"])
        await self.manager.reconcile()
        self.assertEqual(self.sensor.native_value, "GPV3.1")
        self.assertEqual(self.sensor.extra_state_attributes["selection_source"], "cabinet")
        self.assertEqual(self.coord.address_data["schedule_groups"], ["GPV3.1", "GPV5.1"])
        self.assertEqual(self.create.call_count, 1)

    async def test_valid_manual_choice_is_preserved_even_when_cabinet_disagrees(self):
        schedules.AccountScheduleTests.link(self, ["GPV5.1"])
        await self.manager.reconcile()
        self.assertEqual(self.sensor.native_value, "GPV5.1")
        self.hass.config_entries.async_update_entry.assert_not_called()
        self.create.assert_not_called()

    async def test_ambiguous_hidden_failed_and_conflicting_inputs_never_reassign(self):
        schedules.AccountScheduleTests.link(self, ["GPV2.1"])
        for result in (groups.GroupResult("unavailable"), groups.GroupResult("expired"),
                       groups.GroupResult("current", ("GPV3.1", "GPV5.1")),
                       groups.GroupResult("current", ("GPV4.1",))):
            self.result = result
            await self.manager.reconcile()
            self.assertEqual(self.sensor.native_value, "GPV2.1")
        self.coord.address_data["schedule_groups"] = ["GPV5.1"]
        self.coord.address_data["group_labels_visible"] = False
        await self.manager.reconcile()
        self.coord.address_data["group_labels_visible"] = True
        self.coord.address_success = False
        await self.manager.reconcile()
        self.hass.config_entries.async_update_entry.assert_not_called()
        self.create.assert_not_called()

    async def test_temporary_failure_same_recovery_and_user_edit_are_silent(self):
        saved = self.result
        for result in (groups.GroupResult("unavailable"), groups.GroupResult("expired"), saved):
            self.result = result
            await self.manager.reconcile()
        schedules.AccountScheduleTests.link(self, ["GPV5.1"])
        await self.manager.reconcile()
        self.create.assert_not_called()

    async def test_storage_failure_and_concurrent_form_edit_do_not_overwrite_settings(self):
        schedules.AccountScheduleTests.link(self, ["GPV2.1"])
        before = deepcopy(self.personal.options)
        with patch.object(self.manager.store, "async_save", AsyncMock(side_effect=OSError)):
            await self.manager.reconcile()
        self.assertEqual(self.personal.options, before)
        async def edit(_record):
            schedules.AccountScheduleTests.link(self, ["GPV5.1"])
            self.personal.options = deepcopy(self.personal.options)
        with patch.object(self.manager.store, "async_save", side_effect=edit):
            await self.manager.reconcile()
        self.assertEqual(self.sensor.native_value, "GPV5.1")
        self.create.assert_not_called()
        self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_listeners_follow_address_changes_and_are_removed_on_close(self):
        await self.manager.start()
        await self.manager._task
        self.assertIn(self.coord, self.manager._addresses)
        self.result = groups.GroupResult("current", ("GPV5.1",), datetime.now(timezone.utc))
        self.coord.async_update_listeners()
        await self.manager._task
        self.assertEqual(self.create.call_count, 1)
        await self.manager.close()
        self.assertFalse(self.manager._addresses)
        self.result = groups.GroupResult("current", ("GPV3.1",), datetime.now(timezone.utc))
        self.coord.async_update_listeners()
        self.assertEqual(self.create.call_count, 1)

    async def test_source_loss_during_persistence_cannot_apply_a_late_assignment(self):
        schedules.AccountScheduleTests.link(self, ["GPV2.1"])
        async def lose_source(_record):
            self.result = groups.GroupResult("unavailable")
        with patch.object(self.manager.store, "async_save", side_effect=lose_source):
            await self.manager.reconcile()
        self.assertEqual(self.sensor.native_value, "GPV2.1")
        self.hass.config_entries.async_update_entry.assert_not_called()
        self.create.assert_not_called()
        self.assertTrue(self.manager._again)

    async def test_removing_account_dismisses_its_obsolete_notice(self):
        self.result = groups.GroupResult("current", ("GPV5.1",), datetime.now(timezone.utc))
        await self.manager.reconcile()
        notification = self.create.call_args.args[3]
        self.personal.data["accounts"].pop("100001")
        with patch.object(mod.pn, "async_dismiss", wraps=mod.pn.async_dismiss) as dismiss:
            await self.manager.reconcile()
        dismiss.assert_any_call(self.hass, notification)

    async def test_late_source_change_restores_the_persisted_baseline_before_restart(self):
        original_save = self.manager.store.async_save
        self.result = groups.GroupResult("current", ("GPV5.1",), datetime.now(timezone.utc))
        async def save_then_lose_source(record):
            await original_save(record)
            self.result = groups.GroupResult("unavailable")
        with patch.object(self.manager.store, "async_save", side_effect=save_then_lose_source):
            await self.manager.reconcile()
        await self.manager.reconcile()
        stored = await self.manager.store.async_load()
        self.assertEqual(next(iter(stored.values()))["groups"], ["GPV3.1"])
        self.result = groups.GroupResult("current", ("GPV3.1",), datetime.now(timezone.utc))
        restored = mod.AccountScheduleUpdates(self.hass, self.personal, self.account)
        self.addAsyncCleanup(restored.close)
        await restored.start()
        await restored._task
        self.create.assert_not_called()
