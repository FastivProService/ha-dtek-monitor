"""Scheduled dry runs: durable deduplication, account isolation and no billing writes."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_ha_runtime as runtime
from component_loader import load_component_module
from test_account_submission import MemoryStore

if runtime.HAS_HA:
    mod = load_component_module("account_checks")
    flowmod = load_component_module("config_flow")
    account = load_component_module("account")
    sensor = load_component_module("account_sensor")


@unittest.skipUnless(runtime.HAS_HA, "Requires Home Assistant")
class CheckTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.RuntimeTests.asyncSetUp(self)
        self.entry.data = {
            "accounts": {"100001": "First", "100002": "Second"},
            "provider_id": "dtek_oem",
            "entry_kind": "account",
        }
        self.entry.options = {"unrelated": True}
        self.entry.async_start_reauth = Mock()
        self.coord = SimpleNamespace(
            submissions=SimpleNamespace(
                check_source=AsyncMock(
                    return_value={
                        "readings": [
                            {
                                "zone": "GENERAL",
                                "value": 125,
                                "source_at": "2026-10-18T12:00:00+03:00",
                            }
                        ],
                        "binding_revision": "ref",
                    }
                )
            )
        )
        self.manager = mod.AccountChecks(self.hass, self.entry, self.coord)
        self.manager.store = MemoryStore()
        self.addAsyncCleanup(self.manager.close)
        self.now = datetime(2026, 10, 18, 19, tzinfo=mod.KYIV_TZ)

    def enabled(self, number="100001", **options):
        self.entry.options.setdefault(mod.CONF_CHECKS, {})[number] = {
            "enabled": True,
            "time": "19:00:00",
            "frequency": "daily",
            **options,
        }

    async def restart(self):
        await self.manager.close()
        manager = mod.AccountChecks(self.hass, self.entry, self.coord)
        manager.store = self.manager.store
        with patch.object(mod, "datetime", wraps=datetime) as clock:
            clock.now.return_value = self.now
            await manager.start()
        self.manager = manager
        self.addAsyncCleanup(manager.close)

    async def test_disabled_by_default_and_manual_does_not_enable_or_consume_schedule(
        self,
    ):
        await self.manager._tick(self.now)
        self.coord.submissions.check_source.assert_not_awaited()
        result = await self.manager.check_now("100001")
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["schedule_enabled"])
        self.assertFalse(result["submitted"])
        self.assertNotIn(
            "scheduled_day", self.manager.records[self.manager.key("100001")]
        )
        self.assertEqual(self.manager.view("100002")["status"], "idle")
        with self.assertRaisesRegex(mod.SubmissionError, "check_cooldown"):
            await self.manager.check_now("100002")

    async def test_restart_dedup_concurrent_ticks_and_only_today_catchup(self):
        self.enabled()
        await asyncio.gather(self.manager._tick(self.now), self.manager._tick(self.now))
        self.coord.submissions.check_source.assert_awaited_once()
        await self.restart()
        self.coord.submissions.check_source.assert_awaited_once()
        await self.manager._tick(self.now + timedelta(days=40))
        self.assertEqual(self.coord.submissions.check_source.await_count, 2)
        self.assertEqual(len(self.manager.view("100001")["history"]), 2)
        # Backward clock adjustment must not replay an older scheduled day.
        await self.manager._tick(self.now)
        self.assertEqual(self.coord.submissions.check_source.await_count, 2)

    def test_kyiv_calendar_window_and_dst(self):
        config = {"enabled": True, "time": "03:30:00", "frequency": "daily"}
        # The repeated hour in October is one day, even when UTC/fold differs.
        first = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc).astimezone(
            mod.KYIV_TZ
        )
        second = datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc).astimezone(
            mod.KYIV_TZ
        )
        self.assertTrue(mod.due(config, first, None))
        self.assertFalse(mod.due(config, second, first.date().isoformat()))
        # Missing 03:30 in March catches up at 04:00.
        spring = datetime(2026, 3, 29, 1, tzinfo=timezone.utc).astimezone(mod.KYIV_TZ)
        self.assertTrue(mod.due(config, spring, None))
        config["frequency"] = "reading_window"
        for day, expected in [
            (datetime(2028, 2, 27, 19), False),
            (datetime(2028, 2, 28, 19), True),
            (datetime(2028, 2, 29, 19), True),
            (datetime(2028, 3, 3, 19), True),
            (datetime(2028, 3, 4, 19), False),
            (datetime(2026, 12, 31, 19), True),
            (datetime(2027, 1, 1, 19), True),
        ]:
            with self.subTest(day=day):
                self.assertEqual(
                    mod.due(config, day.replace(tzinfo=mod.KYIV_TZ), None), expected
                )

    async def test_one_account_failure_does_not_mix_or_block_another(self):
        self.enabled()
        self.enabled("100002")
        self.coord.submissions.check_source.side_effect = [
            mod.SubmissionError("source_reset"),
            {"readings": [{"zone": "GENERAL", "value": 222}]},
        ]
        await self.manager._tick(self.now)
        first, second = [self.manager.view(n) for n in ("100001", "100002")]
        self.assertEqual(
            (first["status"], first["reason"], first["readings"]),
            ("blocked", "source_reset", []),
        )
        self.assertEqual(second["readings"][0]["value"], 222)
        self.assertNotIn("100001", str(self.manager.store.data))
        # Deselecting stops scheduled work even if old settings remain.
        self.entry.data["accounts"].pop("100001")
        self.coord.submissions.check_source.side_effect = None
        self.coord.submissions.check_source.reset_mock()
        await self.manager._tick(self.now + timedelta(days=1))
        self.assertEqual(
            self.coord.submissions.check_source.call_args.args[0], "100002"
        )
        self.coord.submissions.check_source.assert_awaited_once()

    async def test_cancelled_run_is_interrupted_after_restart_and_not_retried(self):
        self.enabled()
        entered = asyncio.Event()

        async def delayed(*_):
            entered.set()
            await asyncio.Event().wait()

        self.coord.submissions.check_source.side_effect = delayed
        task = asyncio.create_task(self.manager._tick(self.now))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(
            self.manager.store.data[self.manager.key("100001")]["history"][-1][
                "status"
            ],
            "checking",
        )
        self.coord.submissions.check_source.side_effect = None
        await self.restart()
        self.assertEqual(self.manager.view("100001")["status"], "interrupted")
        self.coord.submissions.check_source.assert_awaited_once()

    async def test_broken_store_stops_checks_without_disabling_cabinet(self):
        self.enabled()
        self.manager.store.async_save = AsyncMock(side_effect=OSError("private"))
        await self.manager._tick(self.now)
        self.coord.submissions.check_source.assert_not_awaited()
        self.assertEqual(
            self.manager.view("100001")["reason"], "check_store_unavailable"
        )
        await self.manager._tick(self.now + timedelta(days=1))
        self.manager.store.async_save.assert_awaited_once()
        other = mod.AccountChecks(self.hass, self.entry, self.coord)
        other.store = SimpleNamespace(async_load=AsyncMock(return_value={"broken": {}}))
        await other.start()
        self.assertEqual(other.view("100002")["status"], "error")
        await other.close()

    async def test_final_save_failure_keeps_durable_reservation(self):
        self.enabled()
        save = self.manager.store.async_save
        calls = 0

        async def fail_final(data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk")
            await save(data)

        self.manager.store.async_save = fail_final
        await self.manager._tick(self.now)
        self.assertEqual(
            self.manager.store.data[self.manager.key("100001")]["history"][-1][
                "status"
            ],
            "checking",
        )
        self.manager.store.async_save = save
        await self.restart()
        self.assertEqual(self.manager.view("100001")["status"], "interrupted")
        self.coord.submissions.check_source.assert_awaited_once()

    async def test_unload_during_read_finishes_before_client_closes(self):
        self.enabled()
        entered, gate = asyncio.Event(), asyncio.Event()

        async def delayed(number, authorize):
            entered.set()
            await gate.wait()
            authorize()
            return {}

        self.coord.submissions.check_source.side_effect = delayed
        task = asyncio.create_task(self.manager._tick(self.now))
        await entered.wait()
        closing = asyncio.create_task(self.manager.close())
        await asyncio.sleep(0)
        self.assertFalse(closing.done())
        gate.set()
        await task
        await closing
        self.assertEqual(self.manager.view("100001")["status"], "interrupted")

    async def test_error_reasons_are_sanitized_and_auth_starts_reauth(self):
        self.enabled()
        for i, (error, reason) in enumerate(
            [
                (account.AccountAuthError("secret"), "auth_required"),
                (account.AccountRateLimitError("secret"), "rate_limited"),
                (account.AccountError("secret"), "service_unavailable"),
                (ValueError("secret"), "check_failed"),
            ]
        ):
            self.coord.submissions.check_source.side_effect = error
            await self.manager._tick(self.now + timedelta(days=i))
            self.assertEqual(self.manager.view("100001")["reason"], reason)
            self.assertNotIn("secret", str(self.manager.store.data))
        self.entry.async_start_reauth.assert_called_once()

    async def test_history_is_bounded_and_exposes_original_observation_time(self):
        self.enabled()
        for day in range(24):
            await self.manager._tick(self.now + timedelta(days=day))
        state = sensor.AccountCheckSensor(
            SimpleNamespace(checks=self.manager), self.entry, "100001"
        )
        self.assertEqual(state.native_value, "ready")
        self.assertEqual(len(state.extra_state_attributes["history"]), 20)
        self.assertEqual(
            state.extra_state_attributes["readings"][0]["source_at"],
            "2026-10-18T12:00:00+03:00",
        )
        detached = state.extra_state_attributes
        detached["history"].clear()
        self.assertEqual(len(self.manager.view("100001")["history"]), 20)

    async def test_options_are_per_account_atomic_and_preserve_other_settings(self):
        flow = flowmod.DTEKMonitorOptionsFlow(self.entry)
        flow.hass = self.hass
        result = await flow.async_step_account_checks({"check_account": "other"})
        self.assertIn("base", result["errors"])
        result = await flow.async_step_account_checks({"check_account": "100002"})
        self.assertEqual(result["step_id"], "account_check_settings")
        before = deepcopy(self.entry.options)
        bad = await flow.async_step_account_check_settings(
            {"enabled": True, "time": "12:00+03:00", "frequency": "daily"}
        )
        self.assertIn("base", bad["errors"])
        self.assertEqual(self.entry.options, before)
        result = await flow.async_step_account_check_settings(
            {"enabled": True, "time": "12:00:00", "frequency": "reading_window"}
        )
        self.assertTrue(result["data"]["unrelated"])
        self.assertEqual(set(result["data"][mod.CONF_CHECKS]), {"100002"})
        self.assertEqual(self.entry.options, before)
