"""Exercise the diagnostics platform with real Home Assistant coordinators."""

import json
import unittest

from component_loader import load_component_module
import test_ha_runtime as runtime
import test_account_runtime as account_runtime

diagnostics = load_component_module("diagnostics")


@unittest.skipUnless(runtime.HAS_HA, "Requires real Home Assistant runtime")
class DiagnosticsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_address_refresh_failure_and_recovery_remain_distinct(self):
        await runtime.RuntimeTests.asyncSetUp(self)
        self.entry.runtime_data = self.coord
        await self.coord.async_refresh()
        ready = await diagnostics.async_get_config_entry_diagnostics(self.hass, self.entry)
        self.assertEqual(ready["address"]["connection_state"], "ok")
        self.assertEqual(ready["address"]["transport"]["browser_profile"], "chrome136")
        calls = self.client.get_home_status.call_count
        self.client.get_home_status.side_effect = runtime.client_module.DTEKApiError("private-error-marker")
        await self.coord.async_refresh()
        failed = await diagnostics.async_get_config_entry_diagnostics(self.hass, self.entry)
        self.assertEqual(failed["address"]["connection_state"], "error")
        self.assertTrue(failed["address"]["errors"]["address_error"])
        self.assertEqual(failed["address"]["consecutive_failures"], 1)
        self.assertEqual(self.client.get_home_status.call_count, calls + 1)
        self.assertNotIn("private-error-marker", json.dumps(failed))
        self.assertNotIn(self.entry.data["street"], json.dumps(failed, ensure_ascii=False))
        self.client.get_home_status.side_effect = None
        await self.coord.async_refresh()
        recovered = await diagnostics.async_get_config_entry_diagnostics(self.hass, self.entry)
        self.assertEqual(recovered["address"]["consecutive_failures"], 0)
        self.assertFalse(recovered["address"]["errors"]["address_error"])

    async def test_account_coordinator_partial_availability(self):
        await account_runtime.AccountRuntimeTests.asyncSetUp(self)
        self.entry.runtime_data = self.account_coord
        self.account_client.read_account.side_effect = [self.snapshot, account_runtime.account.AccountError("private-error-marker")]
        await self.account_coord.async_refresh()
        result = await diagnostics.async_get_config_entry_diagnostics(self.hass, self.entry)
        first, second = result["accounts"]["items"]
        self.assertTrue(first["cached_data_present"])
        self.assertFalse(second["cached_data_present"])
        self.account_client.authenticate.assert_awaited_once()
        self.assertEqual(self.account_client.read_account.await_count, 2)
        encoded = json.dumps(result, ensure_ascii=False)
        for value in (self.entry.data["password"], self.entry.data["username"], *self.entry.data["accounts"]):
            self.assertNotIn(value, encoded)
