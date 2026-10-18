"""Synthetic submissions: never write real billing data from tests."""

import asyncio
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from component_loader import load_component_module
from test_account import response

mod = load_component_module("account_submission")
client_module = load_component_module("account_client")
TODAY = date(2026, 10, 18)


def fixture(zones=("GENERAL",)):
    details = {
        "accountNumber": "100001",
        "serialNum": "TEST-METER",
        "digits": "06",
        "address": "Example address",
    }
    meter = {
        "serialNumber": "TEST-METER",
        "isInputReadingsBlocked": False,
        "isGreenTariff": False,
        "disconnectionDate": None,
        "readings": {
            "activeEnergy": [
                {
                    "deviceType": zone,
                    "current": None,
                    "previous": {
                        "isBlocked": False,
                        "readAt": "2026-09-30",
                        "value": 100,
                    },
                }
                for zone in zones
            ]
        },
    }
    return details, {"totalPages": 1, "data": [meter]}


def context(zones=("GENERAL",)):
    return mod.submission_context(*fixture(zones), "100001", TODAY)


class SubmissionParsingTests(unittest.TestCase):
    def test_one_and_two_zones_require_all_integer_readings_with_meter_limits(self):
        for zones in [("GENERAL",), ("DAY", "NIGHT")]:
            ctx = context(zones)
            rows = mod.validate_values(ctx, {z: "123" for z in zones})
            self.assertEqual({r["increase"] for r in rows}, {"23"})
        for values in [
            {},
            {"GENERAL": "99"},
            {"GENERAL": "1000000"},
            {"GENERAL": "123.0"},
            {"GENERAL": "1e3"},
            {"GENERAL": "-1"},
            {"GENERAL": True},
            {"GENERAL": 123},
            {"GENERAL": "NaN"},
            {"GENERAL": "１２３"},
            {"GENERAL": " 123"},
            {"GENERAL": "123", "DAY": "2"},
        ]:
            with self.subTest(values=values), self.assertRaises(mod.SubmissionError):
                mod.validate_values(context(), values)
        self.assertEqual(
            mod.validate_values(context(), {"GENERAL": "100"})[0]["increase"], "0"
        )

    def test_blocked_disconnected_generation_multiple_meters_and_unknown_flags_are_rejected(
        self,
    ):
        for key, value in [
            ("isInputReadingsBlocked", True),
            ("isInputReadingsBlocked", None),
            ("isGreenTariff", True),
            ("isGreenTariff", None),
            ("disconnectionDate", "2026-10-01"),
            ("serialNumber", "CHANGED"),
        ]:
            details, payload = fixture()
            payload["data"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(
                mod.SubmissionError
            ):
                mod.submission_context(details, payload, "100001", TODAY)
        for mutate in [
            lambda d, p: p["data"].append(deepcopy(p["data"][0])),
            lambda d, p: p.update(totalPages=2),
            lambda d, p: d.update(digits="unknown"),
            lambda d, p: p["data"][0]["readings"].update(activeGeneration=[{}]),
            lambda d, p: p["data"][0]["readings"]["activeEnergy"][0].update(
                current={"value": 110}
            ),
            lambda d, p: p["data"][0]["readings"]["activeEnergy"][0]["previous"].update(
                isBlocked=True
            ),
            lambda d, p: p["data"][0]["readings"]["activeEnergy"][0]["previous"].update(
                readAt="2027-01-01"
            ),
        ]:
            d, p = fixture()
            mutate(d, p)
            with self.assertRaises(mod.AccountError):
                mod.submission_context(d, p, "100001", TODAY)

    def test_unknown_missing_duplicate_and_partial_result_never_mean_full_acceptance(
        self,
    ):
        ok = {"index": 0, "status": "SUCCESS", "messageCode": "PRIVATE"}
        self.assertEqual(mod.submission_result([ok], 1), {"status": "accepted"})
        self.assertEqual(
            mod.submission_result([{**ok, "status": "ERROR"}], 1),
            {"status": "rejected"},
        )
        self.assertEqual(
            mod.submission_result([ok, {"index": 1, "status": "ERROR"}], 2),
            {"status": "partial"},
        )
        for payload, count in [
            (None, 1),
            ({"success": True}, 1),
            ([], 1),
            ([ok], 2),
            ([ok, ok], 2),
            ([{"status": "SUCCESS"}], 1),
            ([{**ok, "index": True}], 1),
            ([{**ok, "status": "QUEUED"}], 1),
        ]:
            self.assertEqual(
                mod.submission_result(payload, count), {"status": "unknown"}
            )


class MemoryStore:
    def __init__(self):
        self.data = None

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


class SubmissionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = MemoryStore()
        self.client = SimpleNamespace(
            read_submission_context=AsyncMock(return_value=context()),
            submit_readings=AsyncMock(return_value={"status": "accepted"}),
        )
        self.manager = mod.SubmissionManager(self.client, self.store, "dtek_oem")
        self.allow = Mock()

    async def prepare(self, user="user", value="123"):
        return await self.manager.prepare(
            "100001", user, context()["revision"], {"GENERAL": value}, self.allow
        )

    async def confirm(self, draft, user="user"):
        return await self.manager.confirm(
            "100001", user, draft["confirmation"], self.allow
        )

    async def test_prepare_never_writes_and_confirmation_is_single_use(self):
        d = await self.prepare()
        self.client.submit_readings.assert_not_awaited()
        result = await self.confirm(d)
        self.assertEqual(result["status"], "accepted")
        self.assertNotIn("signature", result)
        with self.assertRaises(mod.SubmissionError):
            await self.confirm(d)
        self.client.submit_readings.assert_awaited_once()
        with self.assertRaisesRegex(mod.SubmissionError, "already_sent"):
            await self.prepare()

    async def test_user_account_scope_and_expiration(self):
        d = await self.prepare()
        with self.assertRaises(mod.SubmissionError):
            await self.confirm(d, "other-user")
        with self.assertRaises(mod.SubmissionError):
            await self.manager.confirm("100002", "user", d["confirmation"], self.allow)
        with patch.object(mod, "monotonic", return_value=float("inf")):
            with self.assertRaises(mod.SubmissionError):
                await self.confirm(d)
        self.client.submit_readings.assert_not_awaited()

    async def test_changed_meter_or_baseline_invalidates_confirmation(self):
        for change in [{"serial": "NEW-METER"}, {"date": "2026-10-19"}, {"digits": 5}]:
            self.client.read_submission_context.return_value = context()
            d = await self.prepare()
            self.client.read_submission_context.return_value = {**context(), **change}
            with self.assertRaisesRegex(mod.SubmissionError, "context_changed"):
                await self.confirm(d)
        self.client.submit_readings.assert_not_awaited()

    async def test_revocation_after_preflight_and_after_journal_write_never_sends(self):
        d = await self.prepare()
        self.allow.side_effect = [None, mod.SubmissionError("unauthorized")]
        with self.assertRaises(mod.SubmissionError):
            await self.confirm(d)
        self.allow.side_effect = None
        d = await self.prepare()
        self.allow.side_effect = [None, None, mod.SubmissionError("unauthorized")]
        with self.assertRaises(mod.SubmissionError):
            await self.confirm(d)
        self.client.submit_readings.assert_not_awaited()

    async def test_pending_persisted_before_post_and_unknown_survives_restart(self):
        async def send(*args):
            self.assertEqual(next(iter(self.store.data.values()))["status"], "pending")
            raise mod.AccountError("private URL/token")

        self.client.submit_readings.side_effect = send
        result = await self.confirm(await self.prepare())
        self.assertEqual(result["status"], "unknown")
        self.manager = mod.SubmissionManager(self.client, self.store, "dtek_oem")
        ctx = await self.manager.context("100001", self.allow)
        self.assertTrue(ctx["blocked"])
        with self.assertRaisesRegex(mod.SubmissionError, "check_cabinet"):
            await self.prepare()
        await self.manager.acknowledge("100001", result["id"], self.allow)
        self.client.submit_readings.assert_awaited_once()
        await self.prepare()

    async def test_partial_success_requires_checking_and_acknowledgment(self):
        self.client.submit_readings.return_value = {"status": "partial"}
        result = await self.confirm(await self.prepare())
        with self.assertRaises(mod.SubmissionError):
            await self.prepare()
        with self.assertRaises(mod.SubmissionError):
            await self.manager.acknowledge("100001", "wrong-id", self.allow)
        await self.manager.acknowledge("100001", result["id"], self.allow)
        await self.prepare()
        self.client.submit_readings.assert_awaited_once()

    async def test_storage_failure_prevents_post_or_preserves_ambiguity(self):
        self.store.async_save = AsyncMock(side_effect=OSError("disk"))
        with self.assertRaisesRegex(mod.SubmissionError, "journal_unavailable"):
            await self.confirm(await self.prepare())
        self.client.submit_readings.assert_not_awaited()
        self.store = MemoryStore()
        self.manager = mod.SubmissionManager(self.client, self.store, "dtek_oem")
        real_save = self.store.async_save

        async def fail_final(data):
            if next(iter(data.values()))["status"] != "pending":
                raise OSError("disk")
            await real_save(data)

        self.store.async_save = fail_final
        self.assertEqual(
            (await self.confirm(await self.prepare()))["status"], "unknown"
        )
        restored = mod.SubmissionManager(self.client, self.store, "dtek_oem")
        self.assertTrue((await restored.context("100001", self.allow))["blocked"])

    async def test_two_concurrent_confirmations_only_send_once(self):
        a, b = await self.prepare(), await self.prepare()
        results = await asyncio.gather(
            self.confirm(a), self.confirm(b), return_exceptions=True
        )
        self.assertEqual(sum(isinstance(r, mod.SubmissionError) for r in results), 1)
        self.client.submit_readings.assert_awaited_once()

    async def test_cancellation_during_post_leaves_durable_pending(self):
        self.client.submit_readings.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.confirm(await self.prepare())
        restored = mod.SubmissionManager(self.client, self.store, "dtek_oem")
        self.assertTrue((await restored.context("100001", self.allow))["blocked"])

    async def test_corrupt_journal_and_closed_manager_fail_closed(self):
        self.store.data = {"key": {"status": "garbage"}}
        with self.assertRaises(mod.SubmissionError):
            await self.prepare()
        self.store.data = None
        await self.manager.close()
        with self.assertRaises(mod.SubmissionError):
            await self.prepare()
        self.client.submit_readings.assert_not_awaited()


class SubmissionTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = client_module.DTEKAccountClient(SimpleNamespace(cookies={}))
        self.client._accounts = {"100001"}
        self.client._ensure_token = AsyncMock()
        self.rows = [{"zone": "GENERAL", "value": 123}]

    async def test_post_body_and_all_failed_responses_are_never_retried(self):
        for response_value in [
            response(200, [{"index": 0, "status": "SUCCESS"}]),
            response(401),
            response(429),
            response(500),
            response(200, {}),
        ]:
            self.client._request = AsyncMock(return_value=response_value)
            result = await self.client.submit_readings("100001", self.rows, Mock())
            self.client._request.assert_awaited_once_with(
                "POST",
                client_module.BILLING + "/meters/household/100001/readings",
                json={
                    "accountNumber": "100001",
                    "readings": [
                        {
                            "deviceEnergyType": "ACTIVE",
                            "deviceScaleType": "GENERAL",
                            "value": 123,
                        }
                    ],
                },
            )
            self.assertEqual(
                result["status"],
                (
                    "accepted"
                    if response_value.status_code == 200
                    and isinstance(response_value.json(), list)
                    else "unknown"
                ),
            )

    async def test_timeout_is_unknown_and_permission_checked_after_token_refresh(self):
        self.client._request = AsyncMock(side_effect=mod.AccountError("timeout"))
        self.assertEqual(
            await self.client.submit_readings("100001", self.rows, Mock()),
            {"status": "unknown"},
        )
        self.client._request.reset_mock()
        with self.assertRaises(mod.SubmissionError):
            await self.client.submit_readings(
                "100001",
                self.rows,
                Mock(side_effect=mod.SubmissionError("unauthorized")),
            )
        self.client._request.assert_not_awaited()
