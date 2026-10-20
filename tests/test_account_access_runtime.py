"""SMS onboarding with real HA forms and a synthetic operator transport."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_account_runtime as cabinet
import test_ha_runtime as runtime
from component_loader import load_component_module
from test_account_access import response

if runtime.HAS_HA:
    import voluptuous as vol

    access = load_component_module("account_access")
    access_flow = load_component_module("account_access_flow")
    account_flow = load_component_module("account_flow")


@unittest.skipUnless(runtime.HAS_HA, "Requires real Home Assistant runtime")
class AccessFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await cabinet.AccountRuntimeTests.asyncSetUp(self)
        self.entry.title = "Synthetic account"
        self.session = SimpleNamespace(
            request=AsyncMock(return_value=response()), close=AsyncMock(), cookies={}
        )
        self.access = access.AccountAccessClient(self.session)
        factory = patch.object(
            access_flow, "AccountAccessClient", return_value=self.access
        )
        factory.start()
        self.addCleanup(factory.stop)
        login = patch.object(
            account_flow, "create_account_provider", return_value=self.account_client
        )
        login.start()
        self.addCleanup(login.stop)

    async def asyncTearDown(self):
        await self.flow._close_access()

    async def start(self, registration=False):
        key = "registration_id" if registration else "recovery_id"
        self.session.request.side_effect = [
            response(200),
            response(200, {key: "synthetic-challenge"}),
        ]
        step = (
            self.flow.async_step_account_register
            if registration
            else self.flow.async_step_account_recover
        )
        result = await step({"username": "+380500000001"})
        self.session.request.side_effect = None
        self.assertEqual(result["step_id"], "account_sms")
        return result

    async def verified(self, registration=False):
        await self.start(registration)
        result = await self.flow.async_step_account_sms({"verification_code": "0123"})
        if registration:
            self.assertEqual(result["step_id"], "account_profile")
            result = await self.flow.async_step_account_profile(
                {"first_name": "Приклад", "last_name": "Тестовий"}
            )
        return result

    async def test_opening_all_entry_points_does_not_send_sms(self):
        result = await self.flow.async_step_account_access()
        self.assertEqual(
            result["menu_options"],
            ["account_login", "account_register", "account_recover"],
        )
        await self.flow.async_step_account_register()
        await self.flow.async_step_account_recover()
        await self.flow.async_step_account_login()
        self.session.request.assert_not_awaited()
        self.account_client.authenticate.assert_not_awaited()

    async def test_unavailable_region_cannot_enter_sms_steps_directly(self):
        self.flow._provider_id = "dtek_dem"
        for method in [
            self.flow.async_step_account_access,
            self.flow.async_step_account_register,
            self.flow.async_step_account_recover,
        ]:
            self.assertEqual((await method())["reason"], "account_not_supported")
        self.session.request.assert_not_awaited()

    async def test_phone_errors_keep_input_without_upstream_calls(self):
        result = await self.flow.async_step_account_recover({"username": "not a phone"})
        self.assertEqual(result["errors"], {"username": "invalid_phone"})
        self.assertEqual(
            next(iter(result["data_schema"].schema)).default(), "not a phone"
        )
        self.session.request.assert_not_awaited()

    async def test_reauth_offers_recovery_only_for_the_original_phone(self):
        self.flow.context = {"source": "reauth", "entry_id": "test-entry"}
        self.hass.config_entries.async_get_known_entry.return_value = self.entry
        result = await self.flow.async_step_reauth(self.entry.data)
        self.assertEqual(result["menu_options"], ["account_login", "account_recover"])
        self.assertEqual(
            (await self.flow.async_step_account_register())["reason"],
            "account_mismatch",
        )
        result = await self.flow.async_step_account_recover(
            {"username": "+380500000002"}
        )
        self.assertEqual(result["errors"]["base"], "access_phone_mismatch")
        self.session.request.assert_not_awaited()

    async def test_verified_code_stops_at_password_confirmation_and_cancel_closes_session(
        self,
    ):
        result = await self.verified()
        self.assertEqual(result["step_id"], "account_recovery_password")
        self.account_client.authenticate.assert_not_awaited()
        fields = {k.schema: k for k in result["data_schema"].schema}
        self.assertIs(fields["password"].default, vol.UNDEFINED)
        self.assertIs(fields["confirm_password"].default, vol.UNDEFINED)
        self.assertFalse(fields["confirm_change"].default())
        self.flow.async_remove()
        await self.hass.async_block_till_done()
        self.session.close.assert_awaited_once()
        self.assertIsNone(self.flow._access_client)
        self.assertFalse(self.access._challenge)
        self.assertFalse(
            any("finalize" in c.args[1] for c in self.session.request.await_args_list)
        )

    async def test_idle_expiration_closes_session_without_background_sms(self):
        with patch.object(access_flow, "ACCESS_IDLE_TIMEOUT", 0.01):
            await self.start()
        await asyncio.sleep(0.03)
        await self.hass.async_block_till_done()
        self.assertIsNone(self.flow._access_client)
        result = await self.flow.async_step_account_sms({"verification_code": "0123"})
        self.assertEqual(result["reason"], "access_expired")
        self.assertEqual(self.session.request.await_count, 2)

    async def test_resend_requires_explicit_choice_and_cooldown(self):
        await self.start()
        result = await self.flow.async_step_account_sms({"resend": True})
        self.assertEqual(result["errors"]["base"], "access_wait")
        self.assertEqual(self.session.request.await_count, 2)
        result = await self.flow.async_step_account_sms({})
        self.assertEqual(result["errors"]["base"], "access_code_format")
        self.assertEqual(self.session.request.await_count, 2)

    async def test_expired_code_aborts_and_clears_challenge(self):
        await self.start()
        self.session.request.return_value = response(404)
        result = await self.flow.async_step_account_sms({"verification_code": "0123"})
        self.assertEqual(result["reason"], "access_expired")
        self.assertIsNone(self.flow._access_client)
        self.assertFalse(self.access._challenge)

    async def test_wrong_code_can_be_corrected_without_new_sms(self):
        await self.start()
        self.session.request.return_value = response(
            400, {"private": "sensitive response"}
        )
        result = await self.flow.async_step_account_sms({"verification_code": "0000"})
        self.assertEqual(result["errors"]["base"], "access_invalid_code")
        self.assertNotIn("sensitive response", str(result))
        self.session.request.return_value = response()
        result = await self.flow.async_step_account_sms({"verification_code": "0123"})
        self.assertEqual(result["step_id"], "account_recovery_password")
        self.assertEqual(
            sum(
                c.args[1].endswith("/start")
                for c in self.session.request.await_args_list
            ),
            1,
        )

    async def test_recovery_requires_consent_and_does_not_echo_password_on_validation_error(
        self,
    ):
        await self.verified()
        result = await self.flow.async_step_account_recovery_password(
            {"password": "EXample123", "confirm_password": "EXample123"}
        )
        self.assertEqual(result["errors"]["base"], "access_confirm_change")
        result = await self.flow.async_step_account_recovery_password(
            {
                "password": "EXample123",
                "confirm_password": "different",
                "confirm_change": True,
            }
        )
        self.assertEqual(result["errors"]["base"], "access_password_mismatch")
        self.assertNotIn("EXample123", str(result))
        self.assertEqual(self.session.request.await_count, 3)

    async def test_recovery_signs_in_and_selects_accounts_without_persisting_sms_state(
        self,
    ):
        await self.verified()
        result = await self.flow.async_step_account_recovery_password(
            {
                "password": "EXample123",
                "confirm_password": "EXample123",
                "confirm_change": True,
            }
        )
        self.assertEqual(result["step_id"], "account_setup")
        self.account_client.authenticate.assert_awaited_once_with(
            "+380500000001", "EXample123"
        )
        result = await self.flow.async_step_accounts({"accounts": ["100001"]})
        self.assertEqual(
            set(result["data"]),
            {
                "entry_kind",
                "provider_id",
                "username",
                "password",
                "account_subject",
                "accounts",
            },
        )
        self.assertNotIn("synthetic-challenge", str(result))
        self.assertIsNone(self.flow._access_client)

    async def test_recovery_updates_existing_connection_only_after_verified_login(self):
        await self.verified()
        self.hass.config_entries.async_entry_for_domain_unique_id.return_value = (
            self.entry
        )
        self.flow.async_update_reload_and_abort = Mock(
            return_value={"type": "abort", "reason": "reauth_successful"}
        )
        result = await self.flow.async_step_account_recovery_password(
            {
                "password": "EXample123",
                "confirm_password": "EXample123",
                "confirm_change": True,
            }
        )
        self.assertEqual(result["reason"], "reauth_successful")
        self.flow.async_update_reload_and_abort.assert_called_once_with(
            self.entry,
            data_updates={"username": "+380500000001", "password": "EXample123"},
        )

    async def test_timeout_after_password_change_never_retries_or_updates_ha_credentials(
        self,
    ):
        await self.verified()
        self.session.request.side_effect = TimeoutError("private data")
        result = await self.flow.async_step_account_recovery_password(
            {
                "password": "EXample123",
                "confirm_password": "EXample123",
                "confirm_change": True,
            }
        )
        self.assertEqual(result["reason"], "access_finalize_uncertain")
        self.account_client.authenticate.assert_not_awaited()
        self.hass.config_entries.async_update_entry.assert_not_called()
        self.assertEqual(self.session.request.await_count, 4)
        self.assertIsNone(self.flow._access_client)

    async def test_new_registration_without_accounts_has_actionable_completion(self):
        result = await self.verified(registration=True)
        self.assertEqual(result["step_id"], "account_registration_password")
        self.account_client.list_accounts.return_value = ()
        result = await self.flow.async_step_account_registration_password(
            {
                "password": "EXample123",
                "confirm_password": "EXample123",
                "accept_terms": True,
            }
        )
        self.assertEqual(result["step_id"], "account_setup")
        self.assertEqual(result["menu_options"], ["account_link", "account_finish_empty"])
        self.assertEqual(self.access.phase, "closed")
        self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_login_failure_after_confirmed_change_does_not_repeat_recovery(self):
        await self.verified()
        self.account_client.authenticate.side_effect = account_flow.AccountError(
            "private upstream error"
        )
        result = await self.flow.async_step_account_recovery_password(
            {
                "password": "EXample123",
                "confirm_password": "EXample123",
                "confirm_change": True,
            }
        )
        self.assertEqual(result["step_id"], "account_access_login")
        self.assertNotIn("EXample123", str(result))
        self.assertEqual(result["errors"]["base"], "account_cannot_connect")
        calls = self.session.request.await_count
        self.account_client.authenticate.side_effect = None
        result = await self.flow.async_step_account_access_login(
            {"username": "+380500000001", "password": "EXample123"}
        )
        self.assertEqual(result["step_id"], "account_setup")
        self.assertEqual(self.session.request.await_count, calls)


if __name__ == "__main__":
    unittest.main()
