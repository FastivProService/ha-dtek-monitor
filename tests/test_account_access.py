"""Synthetic SMS contracts: explicit writes, retry barriers and safe failures."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from component_loader import load_component_module

access = load_component_module("account_access")
account = load_component_module("account")


def response(status=204, data=None, headers=None):
    return SimpleNamespace(status_code=status, json=lambda: data, headers=headers or {})


class AccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = SimpleNamespace(
            request=AsyncMock(), close=AsyncMock(), cookies={}
        )
        self.client = access.AccountAccessClient(self.session)
        self.session.request.return_value = response()

    async def asyncTearDown(self):
        await self.client.close()

    async def start(self, kind="password-recovery"):
        key = "recovery_id" if kind == "password-recovery" else "registration_id"
        self.session.request.side_effect = [
            response(200),
            response(200, {key: "synthetic-challenge"}),
        ]
        await self.client.start(kind, "+380500000001")
        self.session.request.side_effect = None

    async def assert_error(self, code, operation):
        with self.assertRaises(access.AccessError) as caught:
            await operation
        self.assertEqual(caught.exception.code, code)

    async def test_recovery_verification_does_not_change_password(self):
        await self.start()
        await self.client.verify("0123")
        self.assertEqual(self.client.phase, "password")
        self.session.request.assert_awaited_with(
            "POST",
            "https://auth.my.dtek-oem.com.ua/api/oauth/api/auth/password-recovery/verify",
            headers={
                "Accept": "application/json",
                "Origin": "https://auth.my.dtek-oem.com.ua",
                "Referer": "https://auth.my.dtek-oem.com.ua/",
            },
            json={"recovery_id": "synthetic-challenge", "verification_code": "0123"},
        )
        self.assertFalse(
            any(
                "finalize" in call.args[1]
                for call in self.session.request.await_args_list
            )
        )

    async def test_registration_requires_code_profile_password_and_explicit_consent(
        self,
    ):
        await self.start("registration")
        await self.assert_error(
            "access_expired",
            self.client.finalize("EXample123", "EXample123", consent=True),
        )
        await self.client.verify("0123")
        await self.assert_error(
            "access_expired",
            self.client.finalize("EXample123", "EXample123", consent=True),
        )
        await self.client.profile(" Ім’я ", " Приклад ")
        self.assertEqual(
            self.session.request.call_args.kwargs["json"]["first_name"], "Ім’я"
        )
        for consent in [False, None, 1, "true"]:
            await self.assert_error(
                "access_consent_required",
                self.client.finalize("EXample123", "EXample123", consent=consent),
            )
        await self.client.finalize("EXample123", "EXample123", consent=True)
        self.assertTrue(
            self.session.request.call_args.kwargs["json"]["accept_terms_of_service"]
        )
        self.assertEqual(self.client.phase, "complete")
        self.assertEqual(self.client._challenge, "")

    async def test_resend_is_explicit_delayed_and_replaces_challenge(self):
        await self.start()
        count = self.session.request.await_count
        await self.assert_error(
            "access_wait", self.client.start("password-recovery", "+380500000001")
        )
        self.assertEqual(self.session.request.await_count, count)
        with patch.object(
            access.time, "monotonic", return_value=self.client._retry_at + 1
        ):
            self.session.request.side_effect = [
                response(200),
                response(200, {"recovery_id": "new-challenge"}, {"Retry-After": "120"}),
            ]
            await self.client.start("password-recovery", "+380500000001")
            self.assertEqual(self.client.retry_seconds, 120)
        self.assertEqual(self.client._challenge, "new-challenge")

    async def test_bad_code_format_never_calls_service_and_five_checks_are_bounded(
        self,
    ):
        await self.start()
        count = self.session.request.await_count
        for code in ["", "123", "01234", "abcd", "１２３４", 1234]:
            await self.assert_error("access_code_format", self.client.verify(code))
        self.assertEqual(self.session.request.await_count, count)
        self.session.request.return_value = response(
            400, {"secret": "never expose provider data"}
        )
        for _ in range(5):
            await self.assert_error("access_invalid_code", self.client.verify("0123"))
        await self.assert_error("access_attempts_exhausted", self.client.verify("0123"))
        self.assertEqual(self.session.request.await_count, count + 5)

    async def test_no_automatic_retry_on_timeout_after_sms_request(self):
        self.session.request.side_effect = [response(200), TimeoutError("private URL")]
        await self.assert_error(
            "access_start_uncertain",
            self.client.start("password-recovery", "+380500000001"),
        )
        self.assertEqual(self.session.request.await_count, 2)
        self.assertGreater(self.client.retry_seconds, 0)
        await self.assert_error(
            "access_wait", self.client.start("password-recovery", "+380500000001")
        )

    async def test_invalid_start_response_is_not_a_usable_challenge(self):
        for body in [None, [], {}, {"recovery_id": 123}, {"recovery_id": ""}]:
            self.client._retry_at = 0
            self.session.request.side_effect = [response(200), response(200, body)]
            await self.assert_error(
                "access_start_uncertain",
                self.client.start("password-recovery", "+380500000001"),
            )
            self.assertEqual(self.client.phase, "new")
            self.assertFalse(self.client._challenge)

    async def test_service_errors_have_only_safe_distinct_codes(self):
        for status, code in [
            (404, "access_not_registered"),
            (409, "access_already_registered"),
            (429, "access_rate_limit"),
            (403, "access_start_uncertain"),
            (500, "access_start_uncertain"),
        ]:
            self.client._retry_at = 0
            self.session.request.side_effect = [
                response(200),
                response(status, {"private": "provider details"}),
            ]
            await self.assert_error(
                code, self.client.start("password-recovery", "+380500000001")
            )
        await self.start_after_wait()
        self.session.request.return_value = response(404)
        await self.assert_error("access_expired", self.client.verify("0123"))

    async def start_after_wait(self):
        self.client._retry_at = 0
        await self.start()

    async def test_finalization_timeout_cannot_be_replayed(self):
        await self.start()
        await self.client.verify("0123")
        self.session.request.side_effect = TimeoutError("private URL and credentials")
        await self.assert_error(
            "access_finalize_uncertain",
            self.client.finalize("EXample123", "EXample123"),
        )
        count = self.session.request.await_count
        await self.assert_error(
            "access_expired", self.client.finalize("EXample123", "EXample123")
        )
        self.assertEqual(self.session.request.await_count, count)
        self.assertEqual(self.client.phase, "uncertain")
        self.assertFalse(self.client._challenge)

    async def test_concurrent_finalization_is_sent_only_once(self):
        await self.start()
        await self.client.verify("0123")
        self.session.request.reset_mock()
        result = await asyncio.gather(
            self.client.finalize("EXample123", "EXample123"),
            self.client.finalize("EXample123", "EXample123"),
            return_exceptions=True,
        )
        self.assertIsNone(result[0])
        self.assertIsInstance(result[1], access.AccessError)
        self.session.request.assert_awaited_once()

    async def test_password_validation_and_close_clear_secrets(self):
        for password in [
            "short",
            "lowercase123",
            "ONEUPPER1",
            "Oneupper1",
            "NOdigitx",
            "EX ample123",
            "EXample123" * 8,
        ]:
            with self.assertRaises(access.AccessError):
                access.validate_password(password, password)
        with self.assertRaises(access.AccessError):
            access.validate_password("EXample123", "different")
        access.validate_password("АБвгде123", "АБвгде123")
        await self.start()
        await self.client.close()
        self.assertFalse(self.client._challenge)
        self.assertFalse(self.client.phone)
        self.assertEqual(self.client.phase, "closed")


if __name__ == "__main__":
    unittest.main()
