"""Synthetic API changes, partial availability and bounded read-only recovery."""

import asyncio
from datetime import timedelta
from dataclasses import replace
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from component_loader import load_component_module
from test_account import response, token, readings, scale
from test_account_history import page, row
import test_account_runtime as runtime

account = load_component_module("account")
transport = load_component_module("account_client")
history_module = load_component_module("account_history")
PATH = transport.BILLING + "/meters/household/100001/"


class TransportResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.http = SimpleNamespace(cookies={}, request=AsyncMock(), close=AsyncMock())
        self.client = transport.DTEKAccountClient(self.http)
        self.client.subject = "synthetic-subject"
        self.client._token = token()
        self.client._token_expires = time.monotonic() + 900
        self.client._accounts = {"100001"}
        self.client.api_issue_handler = Mock()
        self.client.api_recovered_handler = Mock()
        self.style = "raw"
        self.fail_meter = False
        self.fail_login = False
        self.http.request.side_effect = self.request

    async def request(self, method, url, **kwargs):
        if url.endswith("/api/userData/api/login"):
            return response(401) if self.fail_login else response(data={"xIdToken": token()})
        if url.endswith("/api/oauth/token"):
            return response(401)
        if url.endswith("/get_advanced"):
            return response(data={"status": "success", "data": {"totalPages": 1, "data": [
                {"accountNumber": "100001", "address": "Synthetic address", "isLegal": False}
            ]}})
        expected = ("Bearer " if self.style == "bearer" else "") + self.client._token
        if self.fail_meter or kwargs["headers"].get("X-Id-Token") != expected:
            return response(401, {"code": "UNAUTHORIZED"})
        if method == "POST":
            return response(data=[{"index": 0, "status": "SUCCESS"}])
        if url.endswith("/data"):
            return response(data={"accountNumber": "100001"})
        if url.endswith("/meters-with-readings"):
            return response(data=readings(scale()))
        if url.endswith("/history"):
            return response(data=page(row()))
        if url.endswith("/customer-book"):
            return response(data={"items": []})
        self.fail("Unexpected endpoint")

    def meter_calls(self):
        return [c for c in self.http.request.await_args_list if transport.BILLING in c.args[1]]

    async def test_both_known_formats_recover_and_reuse_without_crossing_cabinet_headers(self):
        for style in ("bearer", "raw"):
            self.style = style
            self.http.request.reset_mock()
            result = await self.client._get(PATH + "data")
            self.assertEqual(result["accountNumber"], "100001")
            self.assertEqual(self.client._meter_auth, style)
            self.assertEqual(len(self.meter_calls()), 3)
            self.http.request.reset_mock()
            await self.client._get(PATH + "meters-with-readings")
            self.assertEqual(len(self.meter_calls()), 1)
            await self.client.list_accounts()
            self.assertEqual(self.http.request.await_args.kwargs["headers"]["X-Id-Token"], self.client._token)
        self.client.api_issue_handler.assert_not_called()

    async def test_concurrent_reads_negotiate_once(self):
        self.style = "bearer"
        results = await asyncio.gather(*(self.client._get(PATH + "data") for _ in range(5)))
        self.assertEqual(len(results), 5)
        self.assertEqual(len(self.meter_calls()), 7)
        renewals = [c for c in self.http.request.await_args_list if c.args[1].endswith("/api/userData/api/login")]
        self.assertEqual(len(renewals), 1)

    async def test_unknown_meter_authorization_is_not_bad_password_and_has_cooldown(self):
        self.fail_meter = True
        with patch.object(transport.time, "monotonic", return_value=100):
            with self.assertRaises(account.AccountAPIChanged) as caught:
                await self.client._get(PATH + "data")
            self.assertEqual(caught.exception.interface, "meter_details")
            self.assertEqual(caught.exception.reason, "authorization_rejected")
            count = self.http.request.await_count
            self.assertEqual(len(self.meter_calls()), 3)
            with self.assertRaises(account.AccountAPIChanged):
                await self.client._get(PATH + "data")
            self.assertEqual(self.http.request.await_count, count)
        self.fail_meter = False
        with patch.object(transport.time, "monotonic", return_value=161):
            result = await self.client.read_account("100001")
        self.assertFalse(result.unavailable)
        self.client.api_recovered_handler.assert_any_call("meter_details")

    async def test_real_session_failure_still_requires_reauth_without_format_probe(self):
        self.fail_meter = self.fail_login = True
        with self.assertRaises(account.AccountAuthError):
            await self.client._get(PATH + "data")
        self.assertEqual(len(self.meter_calls()), 1)
        self.client.api_issue_handler.assert_not_called()

    async def test_network_403_rate_limit_and_500_do_not_switch_format(self):
        for result in (response(403), response(429), response(500), TimeoutError()):
            self.http.request.reset_mock()
            self.http.request.side_effect = [result]
            with self.assertRaises(account.AccountError) as caught:
                await self.client._get(PATH + "data")
            self.assertNotIsInstance(caught.exception, account.AccountAuthError)
            self.assertNotIsInstance(caught.exception, account.AccountAPIChanged)
            self.assertEqual(self.http.request.await_count, 1)
            self.assertEqual(self.client._meter_auth, "raw")

    async def test_negotiated_format_is_used_for_single_submission_and_never_retried(self):
        self.style = "bearer"
        await self.client._get(PATH + "data")
        for status in (200, 401, 429, 500):
            self.http.request.reset_mock()
            self.http.request.side_effect = [response(status, [{"index": 0, "status": "SUCCESS"}])]
            result = await self.client.submit_readings("100001", [{"zone": "GENERAL", "value": 123}], Mock())
            self.http.request.assert_awaited_once()
            call = self.http.request.await_args
            self.assertEqual(call.args[0], "POST")
            self.assertEqual(call.kwargs["headers"]["X-Id-Token"], "Bearer " + self.client._token)
            self.assertEqual(result["status"], "accepted" if status == 200 else "unknown")

    async def test_partial_snapshot_keeps_other_services_and_distinguishes_empty_readings(self):
        for failed in ("meter_details", "meter_readings", "meter_consumption"):
            values = [
                {"accountNumber": "100001", "meterType": "Example"},
                readings(scale()), {"items": []},
            ]
            values[("meter_details", "meter_readings", "meter_consumption").index(failed)] = account.AccountAPIChanged(failed, "HTTP 410")
            self.client._get = AsyncMock(side_effect=values)
            result = await self.client.read_account("100001")
            self.assertEqual(result.unavailable, (failed,))
            self.assertEqual(bool(result.readings), failed != "meter_readings")
            self.assertEqual(result.model, None if failed == "meter_details" else "Example")
        self.client._get = AsyncMock(side_effect=[{"accountNumber": "100001"}, readings(), {"items": []}])
        result = await self.client.read_account("100001")
        self.assertFalse(result.unavailable)
        self.assertEqual(result.readings, ())

    async def test_unrecognized_successful_shapes_raise_repairs_without_inventing_data(self):
        self.client._get = AsyncMock(side_effect=[{}, {"changed": []}, {"changed": []}])
        result = await self.client.read_account("100001")
        self.assertEqual(set(result.unavailable), {"meter_details", "meter_readings", "meter_consumption"})
        self.assertEqual(self.client.api_issue_handler.call_count, 3)
        self.client.api_recovered_handler.assert_not_called()

    async def test_monthly_service_failure_preserves_reading_history(self):
        self.client._get = AsyncMock(side_effect=[page(row()), account.AccountError("offline")])
        result = await self.client.read_history("100001", 2026)
        self.assertEqual(len(result["readings"]), 1)
        self.assertEqual(result["months"], [])
        self.assertEqual(result["unavailable"], ["meter_consumption"])

    async def test_partial_history_cache_retries_after_minute(self):
        client = SimpleNamespace(read_history=AsyncMock(return_value={"unavailable": ["meter_consumption"]}))
        cache = history_module.HistoryCache(client)
        with patch.object(history_module, "monotonic", return_value=100):
            await cache.get("100001", 2026)
        with patch.object(history_module, "monotonic", return_value=130):
            await cache.get("100001", 2026)
        client.read_history.assert_awaited_once()
        with patch.object(history_module, "monotonic", return_value=161):
            await cache.get("100001", 2026)
        self.assertEqual(client.read_history.await_count, 2)


@unittest.skipUnless(runtime.runtime.HAS_HA, "Requires Home Assistant")
class PartialRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.AccountRuntimeTests.asyncSetUp(self)

    async def test_partial_meter_failure_keeps_connection_groups_and_healthy_sensors(self):
        partial = replace(self.snapshot, consumption=None, unavailable=("meter_consumption",))
        self.account_client.read_account.return_value = partial
        await self.account_coord.async_refresh()
        self.assertTrue(self.account_coord.last_update_success)
        self.assertEqual(self.account_coord.update_interval, timedelta(minutes=5))
        sensor = lambda key: runtime.sensors.AccountSensor(self.account_coord, self.entry, "100001", key)
        self.assertFalse(sensor("account_consumption").available)
        self.assertTrue(sensor("account_meter_model").available)
        self.assertTrue(sensor("account_updated").available)
        self.assertEqual(sensor("account_updated").extra_state_attributes["unavailable_services"], ["meter_consumption"])
        self.assertTrue(self.account_coord.groups._available("100001"))
        self.entry.async_start_reauth.assert_not_called()
        self.account_client.read_account.return_value = self.snapshot
        await self.account_coord.async_refresh()
        self.assertTrue(sensor("account_consumption").available)
        self.assertEqual(self.account_coord.update_interval, timedelta(hours=6))

    async def test_all_meter_services_unavailable_still_allow_setup_and_groups(self):
        partial = replace(self.snapshot, model=None, readings=(), consumption=None, profile={"metadata_available": False}, unavailable=("meter_details", "meter_readings", "meter_consumption"))
        self.account_client.read_account.return_value = partial
        with patch.object(runtime.setup_module, "create_account_provider", return_value=self.account_client):
            self.assertTrue(await runtime.setup_module.async_setup_entry(self.hass, self.entry))
            self.assertTrue(self.entry.runtime_data.groups._available("100001"))
            self.entry.async_start_reauth.assert_not_called()
            self.assertTrue(await runtime.setup_module.async_unload_entry(self.hass, self.entry))
