"""Optional meter notices: unchanged, missing and replaced meters across restarts."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_ha_runtime as runtime
from component_loader import load_component_module

if runtime.HAS_HA:
    mod = load_component_module("account_notifications")
    flowmod = load_component_module("account_notification_flow")


@unittest.skipUnless(runtime.HAS_HA, "Requires Home Assistant")
class MeterNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.RuntimeTests.asyncSetUp(self)
        self.entry.data = {
            "accounts": {"100001": "Example apartment"},
            "provider_id": "dtek_oem",
        }
        self.entry.options = {}
        self.hass.config.language = "uk"
        self.coord = SimpleNamespace(
            data={},
            last_update_success=True,
            async_add_listener=Mock(return_value=lambda: None),
        )
        self.snapshot("OLD", 2026)
        self.manager = mod.AccountMeterNotifications(self.hass, self.entry, self.coord)
        self.now = datetime(2026, 10, 18, tzinfo=mod.KYIV_TZ)
        self.addAsyncCleanup(self.manager.close)
        self.create = patch.object(
            mod.pn, "async_create", wraps=mod.pn.async_create
        ).start()
        self.addCleanup(patch.stopall)

    def snapshot(self, serial, year):
        self.coord.data = {
            "100001": SimpleNamespace(
                profile={"serial": serial, "next_verification_year": year}
            )
        }

    def enable(self):
        self.entry.options[mod.CONF_NOTICES] = {
            "100001": {"verification": True, "replacement": True}
        }

    async def start(self, manager=None):
        with patch.object(mod, "datetime", wraps=datetime) as clock:
            clock.now.return_value = self.now
            await (manager or self.manager).start()

    async def test_default_off_baseline_not_an_alert_and_no_fabricated_overdue_date(
        self,
    ):
        await self.start()
        self.create.assert_not_called()
        self.enable()
        await self.manager._tick(self.now)
        self.assertEqual(self.create.call_count, 1)
        self.assertIn("2026", self.create.call_args.args[1])
        self.assertIn("неможливо", self.create.call_args.args[1])
        self.assertNotIn("100001", str(self.create.call_args))
        await self.manager._tick(self.now)
        self.assertEqual(self.create.call_count, 1)
        self.snapshot("OLD", 2028)
        await self.manager._tick(self.now)
        self.assertEqual(self.create.call_count, 1)

    async def test_missing_profile_does_not_reset_baseline_and_replacement_restores_once(
        self,
    ):
        self.enable()
        self.snapshot("OLD", 2028)
        await self.start()
        self.snapshot(None, None)
        await self.manager._tick(self.now)
        self.snapshot("NEW", 2028)
        await self.manager._tick(self.now)
        self.assertEqual(self.create.call_count, 1)
        self.assertIn("змінився", self.create.call_args.args[1])
        self.assertNotIn("OLD", str(self.create.call_args))
        await self.manager._tick(self.now)
        self.assertEqual(self.create.call_count, 1)
        await self.manager.close()
        restored = mod.AccountMeterNotifications(self.hass, self.entry, self.coord)
        self.addAsyncCleanup(restored.close)
        await self.start(restored)
        self.assertEqual(self.create.call_count, 2)
        key = next(iter(restored.records))
        mod.pn.async_dismiss(self.hass, restored.notification_id(key, "replacement"))
        await self.hass.async_block_till_done()
        self.assertTrue(restored.records[key]["events"]["replacement"]["dismissed"])
        await restored.close()
        again = mod.AccountMeterNotifications(self.hass, self.entry, self.coord)
        self.addAsyncCleanup(again.close)
        await self.start(again)
        self.assertEqual(self.create.call_count, 2)
        self.snapshot("OLD", 2028)
        await again._tick(self.now)
        self.assertEqual(self.create.call_count, 3)

    async def test_storage_failure_disabled_and_unselected_notifications(self):
        self.enable()
        self.snapshot("OLD", 2028)
        await self.start()
        self.snapshot("NEW", 2028)
        with patch.object(self.manager.store, "async_save", side_effect=OSError):
            await self.manager._tick(self.now)
        self.create.assert_not_called()
        await self.manager._tick(self.now)
        self.assertEqual(self.create.call_count, 1)
        self.entry.options = {}
        await self.manager._tick(self.now)
        self.assertFalse(next(iter(self.manager.records.values()))["events"])
        self.entry.data["accounts"] = {}
        await self.manager._tick(self.now)
        self.assertFalse(self.manager.records)
        bad = mod.AccountMeterNotifications(self.hass, self.entry, self.coord)
        bad.store = SimpleNamespace(async_load=AsyncMock(return_value={"broken": {}}))
        await self.start(bad)
        self.assertTrue(bad._closed)

    async def test_missing_serial_does_not_repeat_verification_and_new_year_does(self):
        self.enable()
        await self.start()
        self.snapshot(None, 2026)
        await self.manager._tick(self.now)
        self.snapshot("OLD", 2026)
        await self.manager._tick(self.now)
        self.assertEqual(self.create.call_count, 1)
        await self.manager._tick(datetime(2027, 1, 1, tzinfo=mod.KYIV_TZ))
        self.assertEqual(self.create.call_count, 2)

    async def test_dismiss_during_save_cannot_be_overwritten_by_refresh(self):
        self.enable()
        await self.start()
        key = next(iter(self.manager.records))
        # A second account adds a baseline while the first notice is dismissed.
        self.entry.data["accounts"]["100002"] = "Second example"
        started, release = asyncio.Event(), asyncio.Event()
        original = self.manager.store.async_save

        async def slow(data):
            started.set()
            await release.wait()
            await original(data)

        with patch.object(self.manager.store, "async_save", side_effect=slow):
            pending = asyncio.create_task(self.manager._tick(self.now))
            await started.wait()
            self.manager._notification_changed(
                mod.pn.UpdateType.REMOVED,
                {self.manager.notification_id(key, "verification"): {}},
            )
            release.set()
            await pending
            await self.hass.async_block_till_done()
        self.assertTrue(
            self.manager.records[key]["events"]["verification"]["dismissed"]
        )
        self.assertEqual(self.create.call_count, 1)

    async def test_options_are_per_account_opt_in_and_preserve_other_settings(self):
        self.entry.options = {"unrelated": True}
        flow = SimpleNamespace(
            _notice_account="100001",
            async_create_entry=Mock(return_value="saved"),
            async_show_form=Mock(),
            async_abort=Mock(),
        )
        await flowmod.configure(flow, self.entry, None)
        schema = flow.async_show_form.call_args.kwargs["data_schema"]
        self.assertEqual(schema({}), {"verification": False, "replacement": False})
        await flowmod.configure(
            flow, self.entry, {"verification": True, "replacement": False}
        )
        options = flow.async_create_entry.call_args.kwargs["data"]
        self.assertTrue(options["unrelated"])
        self.assertTrue(options[mod.CONF_NOTICES]["100001"]["verification"])
        self.assertEqual(self.entry.options, {"unrelated": True})
