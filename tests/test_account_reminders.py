"""Local reminders: calendar boundaries, restart/dismissal, sources and opt-in."""

import asyncio
from datetime import date, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import test_ha_runtime as runtime
from component_loader import load_component_module

if runtime.HAS_HA:
    mod = load_component_module("account_reminders")
    flowmod = load_component_module("config_flow")
    account = load_component_module("account")
    submission = load_component_module("account_submission")


@unittest.skipUnless(runtime.HAS_HA, "Requires real Home Assistant runtime")
class ReminderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.RuntimeTests.asyncSetUp(self)
        self.hass.config.language = "uk"
        self.entry.data = {
            "accounts": {"100001": "Example apartment"},
            "provider_id": "dtek_oem",
            "entry_kind": "account",
        }
        self.entry.options = {"unrelated": True}
        self.coord = SimpleNamespace(
            energy=SimpleNamespace(
                summary=AsyncMock(
                    return_value={
                        "ready": True,
                        "binding": {"serial": "SYNTHETIC", "zones": {"GENERAL": {}}},
                        "readings": {"GENERAL": {"send_value": "125"}},
                    }
                )
            ),
            client=SimpleNamespace(
                read_submission_context=AsyncMock(
                    return_value={
                        "serial": "SYNTHETIC",
                        "zones": [{"zone": "GENERAL", "previous": "100"}],
                    }
                ),
                submit_readings=AsyncMock(),
            ),
        )
        self.manager = mod.AccountReminders(self.hass, self.entry, self.coord)
        self.create = patch.object(
            mod.pn, "async_create", wraps=mod.pn.async_create
        ).start()
        self.addCleanup(patch.stopall)
        self.addAsyncCleanup(self.manager.close)
        self.now = datetime(2026, 10, 30, 19, 0, tzinfo=mod.KYIV_TZ)

    def enabled(self, **changes):
        self.entry.options[mod.CONF_REMINDERS] = {
            "100001": {
                "enabled": True,
                "time": "19:00:00",
                "include_estimate": True,
                **changes,
            }
        }

    async def start(self, manager=None, now=None):
        with patch.object(mod, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now or self.now
            await (manager or self.manager).start()

    async def restart(self, now=None):
        await self.manager.close()
        replacement = mod.AccountReminders(self.hass, self.entry, self.coord)
        self.addAsyncCleanup(replacement.close)
        await self.start(replacement, now)
        return replacement

    def test_window_calendar_boundaries(self):
        for value, expected in [
            ("2026-10-29", None),
            ("2026-10-30", "2026-10"),
            ("2026-11-03", "2026-10"),
            ("2026-11-04", None),
            ("2026-01-01", "2025-12"),
            ("2028-02-27", None),
            ("2028-02-28", "2028-02"),
            ("2026-02-27", "2026-02"),
            ("2026-04-29", "2026-04"),
        ]:
            window = mod.reading_window(date.fromisoformat(value))
            self.assertEqual(window[0] if window else None, expected)

    async def test_disabled_by_default_and_preview_does_not_consume_period(self):
        await self.start()
        self.create.assert_not_called()
        self.coord.energy.summary.assert_not_awaited()
        await self.manager.preview("100001")
        self.assertEqual(self.create.call_count, 1)
        self.assertIn("Перевірка", self.create.call_args.args[1])
        self.assertEqual(self.manager.records, {})
        self.assertFalse(mod.settings(self.entry, "100001")["enabled"])
        self.coord.client.submit_readings.assert_not_awaited()

    async def test_one_reminder_per_window_and_pending_restores_before_daily_time(self):
        self.enabled()
        await self.start()
        message = self.create.call_args.args[1]
        self.assertIn("125", message)
        self.assertNotIn("100001", message)
        self.assertIn("30.10–03.11", message)
        await asyncio.gather(*(self.manager._tick(self.now) for _ in range(4)))
        self.assertEqual(self.create.call_count, 1)
        # The calendar window spans two months, but is one reporting period.
        restored = await self.restart(datetime(2026, 11, 1, 8, tzinfo=mod.KYIV_TZ))
        self.assertEqual(self.create.call_count, 2)
        self.assertEqual(
            self.create.call_args.args[3],
            self.manager.notification_id(self.manager.key("100001")),
        )
        await restored._tick(datetime(2026, 11, 2, 19, tzinfo=mod.KYIV_TZ))
        self.assertEqual(self.create.call_count, 2)
        self.coord.client.submit_readings.assert_not_awaited()

    async def test_dismissal_is_durable_and_new_month_gets_a_reminder(self):
        self.enabled()
        await self.start()
        mod.pn.async_dismiss(
            self.hass, self.manager.notification_id(self.manager.key("100001"))
        )
        await self.hass.async_block_till_done()
        self.assertTrue(self.manager.records[self.manager.key("100001")]["dismissed"])
        restored = await self.restart()
        self.assertEqual(self.create.call_count, 1)
        await restored._tick(datetime(2026, 11, 29, 19, tzinfo=mod.KYIV_TZ))
        self.assertEqual(self.create.call_count, 2)

    async def test_no_early_reminders_and_catch_up_only_inside_window(self):
        self.enabled()
        await self.start(now=datetime(2026, 10, 30, 18, 59, tzinfo=mod.KYIV_TZ))
        self.create.assert_not_called()
        await self.manager._tick(datetime(2026, 11, 4, 19, tzinfo=mod.KYIV_TZ))
        self.create.assert_not_called()
        await self.manager._tick(datetime(2026, 11, 1, 20, tzinfo=mod.KYIV_TZ))
        self.create.assert_called_once()

    async def test_estimate_failure_or_replaced_meter_never_shows_invalid_value(self):
        self.enabled()
        for mode in (
            "unavailable",
            "reset",
            "cabinet_offline",
            "meter_replaced",
            "reading_lower",
        ):
            with self.subTest(mode=mode):
                self.coord.energy.summary.side_effect = None
                self.coord.client.read_submission_context.side_effect = None
                self.coord.client.read_submission_context.return_value = {
                    "serial": "SYNTHETIC",
                    "zones": [{"zone": "GENERAL", "previous": "100"}],
                }
                if mode in ("unavailable", "reset"):
                    self.coord.energy.summary.side_effect = submission.SubmissionError(
                        "source_" + mode
                    )
                elif mode == "cabinet_offline":
                    self.coord.client.read_submission_context.side_effect = (
                        account.AccountError("private details")
                    )
                elif mode == "meter_replaced":
                    self.coord.client.read_submission_context.return_value["serial"] = (
                        "REPLACED"
                    )
                else:
                    self.coord.client.read_submission_context.return_value["zones"][0][
                        "previous"
                    ] = "200"
                _, message = await self.manager.message("100001", self.now)
                self.assertNotIn("125", message)
                self.assertNotIn("private details", message)
                self.assertIn("вручну", message)
        self.coord.client.submit_readings.assert_not_awaited()

    async def test_estimate_is_optional_and_no_binding_needs_no_dtek_request(self):
        self.enabled(include_estimate=False)
        await self.manager.message("100001", self.now)
        self.coord.energy.summary.assert_not_awaited()
        self.coord.client.read_submission_context.assert_not_awaited()
        self.enabled()
        self.coord.energy.summary.return_value = {"binding": None}
        await self.manager.message("100001", self.now)
        self.coord.client.read_submission_context.assert_not_awaited()

    async def test_disabling_dismisses_notification_without_marking_period_complete(
        self,
    ):
        self.enabled()
        await self.start()
        self.enabled(enabled=False)
        await self.manager._tick(self.now)
        await self.hass.async_block_till_done()
        self.assertFalse(self.manager.records[self.manager.key("100001")]["dismissed"])
        self.assertFalse(self.manager._shown)

    async def test_storage_error_does_not_send_or_disable_account(self):
        self.enabled()
        with patch.object(
            self.manager.store, "async_save", AsyncMock(side_effect=OSError("disk"))
        ):
            await self.start()
        self.create.assert_not_called()
        await self.manager._tick(self.now)
        self.create.assert_called_once()
        await self.manager.close()
        other = mod.AccountReminders(self.hass, self.entry, self.coord)
        with patch.object(
            other.store, "async_load", AsyncMock(return_value=["invalid"])
        ):
            await self.start(other)
        self.assertTrue(other._closed)
        self.assertEqual(self.create.call_count, 1)

    async def test_reload_while_awaiting_source_cannot_deliver_after_unload(self):
        self.enabled()
        gate = asyncio.Event()
        entered = asyncio.Event()

        async def delayed(_):
            entered.set()
            await gate.wait()
            return {"binding": None}

        self.coord.energy.summary.side_effect = delayed
        task = asyncio.create_task(self.manager._tick(self.now))
        await entered.wait()
        await self.manager.close()
        gate.set()
        await task
        self.create.assert_not_called()

    async def test_accounts_have_independent_notifications_and_dismissals(self):
        self.enabled()
        self.entry.data["accounts"]["100002"] = "Second apartment"
        self.entry.options[mod.CONF_REMINDERS]["100002"] = dict(
            self.entry.options[mod.CONF_REMINDERS]["100001"]
        )
        await self.start()
        self.assertEqual(self.create.call_count, 2)
        self.assertNotEqual(
            self.create.call_args_list[0].args[3], self.create.call_args_list[1].args[3]
        )
        mod.pn.async_dismiss(
            self.hass, self.manager.notification_id(self.manager.key("100001"))
        )
        await self.hass.async_block_till_done()
        self.assertTrue(self.manager.records[self.manager.key("100001")]["dismissed"])
        self.assertFalse(self.manager.records[self.manager.key("100002")]["dismissed"])

    async def test_utc_clock_is_evaluated_as_kyiv_calendar_date(self):
        self.enabled(time="00:00:00", include_estimate=False)
        # On Oct 29, 22:00 UTC it is already Oct 30 in Kyiv (winter time).
        await self.start(now=datetime.fromisoformat("2026-10-29T22:00:00+00:00"))
        self.create.assert_called_once()
        self.assertIn("2026-10", self.create.call_args.args[1])

    async def test_unload_during_storage_write_does_not_deliver(self):
        self.enabled(include_estimate=False)
        gate = asyncio.Event()
        entered = asyncio.Event()

        async def saving(_):
            entered.set()
            await gate.wait()

        with patch.object(self.manager.store, "async_save", side_effect=saving):
            task = asyncio.create_task(self.manager._tick(self.now))
            await entered.wait()
            await self.manager.close()
            gate.set()
            await task
        self.create.assert_not_called()

    async def test_settings_keep_other_options_and_recheck_account(self):
        flow = flowmod.DTEKMonitorOptionsFlow(self.entry)
        flow.hass = self.hass
        first = await flow.async_step_account_reminders({"reminder_account": "100001"})
        self.assertEqual(first["step_id"], "account_reminder_settings")
        self.assertEqual(self.entry.options, {"unrelated": True})
        result = await flow.async_step_account_reminder_settings(
            {"enabled": True, "time": "20:30:00", "include_estimate": False}
        )
        self.assertTrue(result["data"]["unrelated"])
        self.assertEqual(
            result["data"][mod.CONF_REMINDERS]["100001"]["time"], "20:30:00"
        )
        invalid = await flow.async_step_account_reminder_settings(
            {"enabled": True, "time": "25:00:00", "include_estimate": False}
        )
        self.assertEqual(invalid["errors"]["base"], "invalid_reminder_settings")
        self.entry.data["accounts"] = {}
        result = await flow.async_step_account_reminder_settings(
            {"enabled": True, "time": "20:30:00", "include_estimate": False}
        )
        self.assertEqual(result["reason"], "no_accounts")
