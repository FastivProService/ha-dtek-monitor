"""Synthetic account records: billing semantics and transport failure boundaries."""

import asyncio
import base64
from datetime import date
from decimal import Decimal
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from component_loader import load_component_module

account = load_component_module("account")
client_module = load_component_module("account_client")
TODAY = date(2026, 10, 18)


def readings(*scales):
    return {"data": [{"readings": {"activeEnergy": list(scales)}}], "totalPages": 1}


def scale(zone="GENERAL", value=123, read_at="2026-09-30", blocked=False):
    return {
        "deviceType": zone,
        "previous": {
            "value": value,
            "readAt": read_at,
            "isBlocked": blocked,
        },
        "current": {"value": 999, "readAt": "2026-10-01"},
    }


def response(status=200, data=None, headers=None):
    return SimpleNamespace(status_code=status, json=lambda: data, headers=headers or {})


def token():
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 1020}).encode())
        .decode()
        .rstrip("=")
    )
    return "header." + payload + ".signature"


class ParsingTests(unittest.TestCase):
    def test_pending_current_reading_never_replaces_previous(self):
        result = account.parse_readings(readings(scale()), TODAY)
        self.assertEqual(result[0].value, Decimal(123))
        self.assertEqual(result[0].read_at, date(2026, 9, 30))

    def test_blocked_or_unknown_acceptance_is_not_a_reading(self):
        for blocked in [True, None, "false", 0]:
            self.assertEqual(
                account.parse_readings(readings(scale(blocked=blocked)), TODAY), ()
            )

    def test_missing_previous_does_not_use_current(self):
        value = scale()
        value["previous"] = None
        self.assertEqual(account.parse_readings(readings(value), TODAY), ())

    def test_zones_remain_separate_even_with_different_dates(self):
        result = account.parse_readings(
            readings(scale("NIGHT", 20), scale("DAY", 10, "2026-09-29")), TODAY
        )
        self.assertEqual(
            [(r.zone, r.value) for r in result], [("DAY", 10), ("NIGHT", 20)]
        )
        self.assertNotEqual(result[0].read_at, result[1].read_at)

    def test_invalid_meter_records_fail_instead_of_returning_zero(self):
        cases = [
            None,
            [],
            {},
            {"data": None},
            {"data": [None]},
            readings(scale(), scale()),
            readings(scale(value="NaN")),
            readings(scale(value=True)),
            readings(scale(value=-1)),
            readings(scale(read_at="2026-11-01")),
            readings(scale(read_at="not a date")),
            readings(scale(zone="../secret")),
        ]
        multiple = readings(scale())
        multiple["totalPages"] = 2
        cases.append(multiple)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(account.AccountError):
                account.parse_readings(value, TODAY)

    def test_signed_monthly_correction_is_preserved_with_its_period(self):
        result = account.parse_consumption(
            {
                "items": [
                    {"date": "2026-10", "consumption": 500},
                    {"date": "2026-09", "consumption": -12},
                    {"date": "2026-08", "consumption": 42},
                ]
            },
            TODAY,
        )
        self.assertEqual(result, (Decimal(-12), "2026-09"))

    def test_empty_and_invalid_consumption_are_distinct(self):
        self.assertEqual(account.parse_consumption({"items": []}, TODAY), (None, None))
        for data in [
            {},
            {"items": None},
            {"items": [{"date": "2026-13", "consumption": 0}]},
            {"items": [{"date": "2026-09", "consumption": "nan"}]},
            {"items": [{"date": "2026-09", "consumption": 1}] * 2},
        ]:
            with self.assertRaises(account.AccountError):
                account.parse_consumption(data, TODAY)

    def test_phone_and_identity_are_independent_of_display_strings(self):
        self.assertEqual(
            account.normalize_phone("+38 (050) 000-00-01"), "+380500000001"
        )
        with self.assertRaises(ValueError):
            account.normalize_phone("555")
        self.assertNotEqual(
            account.account_key("dtek_oem", "subject"),
            account.account_key("dtek_kem", "subject"),
        )
        self.assertNotIn("subject", account.account_key("dtek_oem", "subject"))
        with self.assertRaises(account.AccountError):
            client_module.create_account_provider("dtek_kem")


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = SimpleNamespace(
            cookies={}, request=AsyncMock(), close=AsyncMock()
        )
        self.client = client_module.DTEKAccountClient(self.session)
        self.client.subject = "synthetic-subject"
        self.client._token = token()
        self.client._token_expires = time.monotonic() + 900

    async def test_cold_login_validates_pkce_state_and_does_not_follow_redirects(self):
        async def request(method, url, **kwargs):
            if url.endswith("/authorize"):
                params = kwargs["params"]
                self.assertEqual(params["code_challenge_method"], "S256")
                self.assertGreaterEqual(len(params["code_challenge"]), 43)
                return response(
                    302,
                    headers={
                        "location": self.client.spec.callback
                        + "?code=synthetic-code&state="
                        + params["state"]
                    },
                )
            if url.endswith("/userinfo"):
                return response(data={"sub": "synthetic-subject"})
            if url.endswith("/api/userData/api/login"):
                return response(data={"xIdToken": token()})
            return response()

        self.session.request.side_effect = request
        self.assertEqual(
            await self.client.authenticate("+380500000001", "synthetic-test-password"),
            "synthetic-subject",
        )
        self.assertEqual(self.session.request.await_count, 6)

    async def test_redirect_attack_or_state_mismatch_does_not_exchange_code(self):
        for location in [
            "https://example.invalid/oauth/callback?code=x&state=x",
            self.client.spec.callback + "?code=x&state=wrong",
        ]:
            self.session.request.reset_mock()
            self.session.request.side_effect = [
                response(),
                response(),
                response(302, headers={"location": location}),
            ]
            with self.assertRaises(account.AccountAuthError):
                await self.client.authenticate(
                    "+380500000001", "synthetic-test-password"
                )
            self.assertEqual(self.session.request.await_count, 3)

    async def test_invalid_credentials_are_not_retried(self):
        self.session.request.side_effect = [response(), response(401)]
        with self.assertRaises(account.AccountAuthError):
            await self.client.authenticate("+380500000001", "synthetic-test-password")
        self.assertEqual(self.session.request.await_count, 2)

    async def test_expired_api_token_renews_once_for_concurrent_readers(self):
        self.client._token_expires = 0
        self.session.request.return_value = response(data={"xIdToken": token()})
        await asyncio.gather(*(self.client._ensure_token() for _ in range(8)))
        self.assertEqual(self.session.request.await_count, 1)

    async def test_oauth_refresh_then_xid_renewal_uses_csrf(self):
        self.client._token_expires = 0
        self.session.cookies["XSRF-TOKEN"] = "synthetic%20csrf"
        self.session.request.side_effect = [
            response(401),
            response(),
            response(data={"xIdToken": token()}),
        ]
        await self.client._ensure_token()
        calls = self.session.request.call_args_list
        self.assertEqual(calls[1].kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(calls[1].kwargs["headers"]["X-CSRF"], "synthetic csrf")

    async def test_get_401_renews_and_retries_only_once(self):
        self.session.request.side_effect = [
            response(401),
            response(data={"xIdToken": token()}),
            response(401),
        ]
        with self.assertRaises(account.AccountAuthError):
            await self.client._get("/api/example")
        self.assertEqual(self.session.request.await_count, 3)

    async def test_403_and_429_never_trigger_login_or_refresh(self):
        for status, error in [
            (403, account.AccountError),
            (429, account.AccountRateLimitError),
        ]:
            self.session.request.reset_mock()
            self.session.request.side_effect = None
            self.session.request.return_value = response(status)
            with self.assertRaises(error):
                await self.client._get("/api/example")
            self.assertEqual(self.session.request.await_count, 1)

    async def test_list_paginates_and_skips_business_accounts(self):
        def page(number, legal=False):
            return response(
                data={
                    "status": "success",
                    "error": None,
                    "data": {
                        "totalPages": 2,
                        "data": [
                            {
                                "accountNumber": number,
                                "address": "м. Приклад, вул. Тестова буд. 1",
                                "isLegal": legal,
                            }
                        ],
                    },
                }
            )

        self.session.request.side_effect = [page("100001"), page("100002", True)]
        result = await self.client.list_accounts()
        self.assertEqual([a.number for a in result], ["100001"])
        self.assertEqual(
            self.session.request.call_args_list[1].kwargs["params"]["page"], 2
        )

    async def test_unlinked_account_is_never_requested(self):
        with self.assertRaises(account.AccountError):
            await self.client.read_account("100001")
        self.session.request.assert_not_called()

    async def test_invalid_token_and_html_response_are_sanitized(self):
        self.client._token_expires = 0
        self.session.request.return_value = response(
            data={"xIdToken": "do-not-echo-secret"}
        )
        with self.assertRaises(account.AccountError) as ctx:
            await self.client._ensure_token()
        self.assertNotIn("do-not-echo", str(ctx.exception))
        with self.assertRaises(account.AccountError):
            self.client._decode(
                SimpleNamespace(
                    status_code=200, json=lambda: json.loads("<html>secret</html>")
                )
            )

    async def test_january_requests_previous_year_and_unknown_is_not_zero(self):
        self.client._accounts = {"100001"}
        details = {
            "accountNumber": "100001",
            "meterType": "Example",
            "nextVerificationYear": "2030",
        }
        self.session.request.side_effect = [
            response(data=details),
            response(data=readings(scale())),
            response(data={"items": []}),
        ]
        from datetime import datetime

        with patch.object(client_module, "datetime") as clock:
            clock.now.return_value = datetime(
                2027, 1, 1, tzinfo=client_module.ZoneInfo("Europe/Kyiv")
            )
            result = await self.client.read_account("100001")
        self.assertIsNone(result.consumption)
        self.assertEqual(self.session.request.call_args.kwargs["params"]["year"], 2026)

    async def test_close_discards_token_and_membership(self):
        await self.client.close()
        self.session.close.assert_awaited_once()
        self.assertFalse(self.client._token)
        self.assertFalse(self.client._accounts)
