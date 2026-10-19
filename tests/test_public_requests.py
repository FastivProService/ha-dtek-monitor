"""Public requests share an operator cooldown without sharing private sessions."""

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from component_loader import load_component_module

requests = load_component_module("public_requests")
client = load_component_module("dtek_client")
providers = load_component_module("providers")


class Clock:
    value = 100.0

    def __call__(self):
        return self.value

    async def sleep(self, seconds):
        self.value += seconds


class PublicRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = Clock()
        self.gate = requests.PublicRequestGate(clock=self.clock, sleep=self.clock.sleep)

    def http_client(self, response):
        instance = client.DTEKClient(request_gate=self.gate)
        instance._csrf_token = "fixture"
        instance._session_created = datetime.now(client.KYIV_TZ)
        session = SimpleNamespace(post=AsyncMock(return_value=response), close=AsyncMock())
        instance._http = session
        return instance, session

    async def test_waiting_addresses_do_not_send_after_first_failure(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked():
            entered.set()
            await release.wait()
            raise requests.PublicRequestError("HTTP 429")

        first = asyncio.create_task(self.gate.run(blocked))
        await entered.wait()
        peer = AsyncMock()
        second = asyncio.create_task(self.gate.run(peer))
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertIsInstance(results[0], requests.PublicRequestError)
        self.assertIsInstance(results[1], requests.PublicCooldownError)
        peer.assert_not_awaited()
        self.assertEqual(self.gate.failures, 1)

    async def test_repeated_failures_back_off_but_suppressed_calls_do_not_extend_pause(self):
        operation = AsyncMock(side_effect=requests.PublicRequestError("down"))
        for delay in (60, 120, 240, 480, 600, 600):
            with self.assertRaises(requests.PublicRequestError):
                await self.gate.run(operation)
            self.assertEqual(self.gate.retry_in, delay)
            attempts = operation.await_count
            self.clock.value += 1
            for _ in range(5):
                with self.assertRaises(requests.PublicCooldownError):
                    await self.gate.run(operation)
            self.assertEqual(operation.await_count, attempts)
            self.assertEqual(self.gate.retry_in, delay - 1)
            self.clock.value += delay - 1
        self.assertEqual(await self.gate.run(AsyncMock(return_value="recovered")), "recovered")
        self.assertEqual(self.gate.failures, 0)
        self.assertEqual(self.gate.retry_in, 0)
        with self.assertRaises(requests.PublicRequestError):
            await self.gate.run(operation)
        self.assertEqual(self.gate.retry_in, 60)

    async def test_successful_transactions_are_serialized_and_spaced(self):
        times = []

        async def operation():
            times.append(self.clock())
            await asyncio.sleep(0)
            return len(times)

        self.assertEqual(await asyncio.gather(*(self.gate.run(operation) for _ in range(3))), [1, 2, 3])
        self.assertEqual(times, [100, 101, 102])

    async def test_cancellation_releases_gate_without_backoff(self):
        entered = asyncio.Event()

        async def wait():
            entered.set()
            await asyncio.Future()

        task = asyncio.create_task(self.gate.run(wait))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(await self.gate.run(AsyncMock(return_value=1)), 1)
        self.assertEqual(self.gate.failures, 0)

    async def test_cancelled_waiter_does_not_cancel_another_address(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def hold():
            entered.set()
            await release.wait()

        task = asyncio.create_task(self.gate.run(hold))
        await entered.wait()
        peer = AsyncMock()
        waiter = asyncio.create_task(self.gate.run(peer))
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        release.set()
        await task
        peer.assert_not_awaited()
        self.assertEqual(await self.gate.run(AsyncMock(return_value=2)), 2)

    async def test_reload_keeps_pause_other_operators_and_ha_instances_are_independent(self):
        hass = SimpleNamespace(data={})
        a = providers.create_provider("dtek_oem", hass=hass)
        gate = a._client.request_gate
        with self.assertRaises(requests.PublicRequestError):
            await gate.run(AsyncMock(side_effect=requests.PublicRequestError("down")))
        await a.close()
        b = providers.create_provider("dtek_oem", hass=hass)
        other = providers.create_provider("dtek_krem", hass=hass)
        independent = providers.create_provider("dtek_oem", hass=SimpleNamespace(data={}))
        self.assertIs(gate, b._client.request_gate)
        with self.assertRaises(requests.PublicCooldownError):
            await b._client._post({"method": "getStreets"})
        self.assertIsNone(b._client._http)
        for instance in (other, independent):
            self.assertEqual(await instance._client.request_gate.run(AsyncMock(return_value=1)), 1)

    async def test_429_honors_server_pause_for_a_second_client_without_http(self):
        response = SimpleNamespace(status_code=429, headers={"Retry-After": "900"})
        first, session = self.http_client(response)
        second, peer = self.http_client(None)
        with self.assertRaises(client.DTEKApiError):
            await first._post({"method": "getHomeNum"})
        self.assertEqual(self.gate.retry_in, 900)
        with self.assertRaises(requests.PublicCooldownError):
            await second._post({"method": "checkDisconUpdate"})
        session.post.assert_awaited_once()
        peer.post.assert_not_awaited()

    async def test_503_on_initial_page_sets_gate_without_post_or_retry(self):
        instance = client.DTEKClient(request_gate=self.gate)
        instance._http = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(
            status_code=503, text="unavailable", headers={"Retry-After": "300"}
        )), post=AsyncMock())
        with self.assertRaises(client.DTEKApiError):
            await instance._post({"method": "getStreets"})
        self.assertEqual(self.gate.retry_in, 300)
        instance._http.get.assert_awaited_once()
        instance._http.post.assert_not_awaited()

    async def test_no_schedule_delta_is_a_success_not_an_operator_failure(self):
        response = SimpleNamespace(status_code=200, text='{"result": false}', json=lambda: {"result": False})
        instance, _ = self.http_client(response)
        self.assertIsNone(await instance.check_update("19.10.2026 10:00"))
        self.assertEqual(self.gate.retry_in, 0)

    async def test_bad_json_cannot_reset_failure_cooldown(self):
        response = SimpleNamespace(status_code=200, text="broken", json=lambda: [])
        instance, _ = self.http_client(response)
        with self.assertRaises(client.DTEKApiError):
            await instance._post({"method": "getStreets"})
        self.assertEqual(self.gate.retry_in, 60)

    def test_retry_after_parsing_handles_dates_invalid_and_extreme_values(self):
        parse = requests.retry_after_seconds
        self.assertEqual(parse("120"), 120)
        self.assertEqual(parse("9999999999"), 3600)
        self.assertEqual(parse("-10"), 0)
        for value in (None, "nan", "inf", "tomorrow", "1e999"):
            self.assertIsNone(parse(value))
        value = parse(format_datetime(datetime.now(timezone.utc) + timedelta(seconds=90)))
        self.assertGreater(value, 88)
        self.assertLessEqual(value, 90)
