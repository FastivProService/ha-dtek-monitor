"""Synthetic legacy handoff and group responses; no live credentials."""

import base64
import json
from types import SimpleNamespace as NS
from urllib.parse import urlencode
import unittest
from unittest.mock import AsyncMock, Mock, patch

from component_loader import load_component_module
from test_account import response

mod = load_component_module("account_group_client")
transport = load_component_module("account_client")
regions = load_component_module("account_providers")
account = load_component_module("account")
NUMBER = "572000000001"
OTHER = "572000000002"
EIC = "00TEST0000000001"


def info(number=NUMBER):
    return response(data={"status": "success", "customer": {"account": number, "eic": EIC}})


def group_response(groups="GPV3.1"):
    return response(data={"respType": "SUCCESS", "gpv": groups})


class GroupParsingTests(unittest.TestCase):
    def test_single_multiple_array_empty_and_duplicate_groups(self):
        for raw, expected in (("GPV3.1", ("GPV3.1",)), ("ГПВ57.1", ("GPV57.1",)), (" ГПВ5.1,GPV3.1,GPV3.1 ", ("GPV3.1", "GPV5.1")),
                              (["GPV5.1", "GPV3.1"], ("GPV3.1", "GPV5.1")), (None, ()), ("", ()), ([], ())):
            with self.subTest(raw=raw):
                self.assertEqual(mod.parse_groups({"respType": "SUCCESS", "gpv": raw}), expected)

    def test_errors_and_unknown_groups_never_become_empty_or_partial(self):
        for payload in (None, [], {}, {"respType": "SUCCESS"}, {"respType": "FAIL", "gpv": "GPV3.1"},
                        *({"respType": "SUCCESS", "gpv": raw} for raw in (7, {}, [None], "GPV3.1,UNKNOWN", "GPV3.1,", "GPV0.1", "GPV007.1", "GPV7.3", "GPV100.1"))):
            with self.subTest(payload=payload), self.assertRaises(account.AccountError):
                mod.parse_groups(payload)


class GroupClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_experimental_regions_use_own_group_handoff_and_verify_identity(self):
        for provider, number, site in (
            ("dtek_krem", "842000000001", "krem"),
            ("dtek_dnem", "120000000001", "dnem"),
        ):
            with self.subTest(provider=provider):
                self.modern._spec = regions.ACCOUNT_PROVIDER_SPECS[provider]
                self.modern._accounts = {number}
                self.modern._linked_profiles = {number: {"eic": [EIC]}}
                self.modern_session.request.reset_mock()
                self.session.request.reset_mock()
                with patch.object(mod, "AsyncSession", return_value=self.session):
                    client = mod.create_account_group_provider(self.modern)
                self.assertIsNotNone(client)
                self.session.request.side_effect = [
                    self.login, info(number), group_response(["GPV3.1", "GPV5.1"]),
                ]
                self.assertEqual(await client.read_groups(number), ("GPV3.1", "GPV5.1"))
                grant = self.modern_session.request.call_args
                self.assertEqual(grant.args[1], f"https://auth.my.dtek-{site}.com.ua/api/oauth/authorize")
                self.assertEqual(grant.kwargs["params"]["redirect_uri"], f"https://ok.dtek-{site}.com.ua/auth/callback")
                login, profile, groups = self.session.request.call_args_list
                self.assertEqual(login.kwargs["json"]["site"], site)
                for call in (login, profile, groups):
                    self.assertTrue(call.args[1].startswith(f"https://ok.dtek-{site}.com.ua/api/"))
                    self.assertFalse(call.kwargs["allow_redirects"])
                self.assertEqual(groups.kwargs["json"]["eic"], EIC)
                self.session.request.reset_mock()
                self.session.request.side_effect = [
                    response(data={"status": "success", "customer": {
                        "account": number, "eic": "00TEST0000000002",
                    }}),
                ]
                with self.assertRaises(account.AccountError):
                    await client.read_groups(number)
                self.assertEqual(self.session.request.await_count, 1)
                self.modern.api_issue_handler.assert_not_called()
                await client.close()

    async def test_kyiv_legacy_handoff_uses_verified_host_and_site(self):
        number = "452000000001"
        self.modern._spec = regions.ACCOUNT_PROVIDER_SPECS["dtek_kem"]
        self.modern._accounts = {number}
        client = mod.AccountGroupClient(self.modern, self.session)
        self.session.request.side_effect = [self.login, info(number), group_response("ГПВ57.1")]
        self.assertEqual(await client.read_groups(number), ("GPV57.1",))
        call = self.modern_session.request.call_args
        self.assertEqual(call.args[1], "https://auth.my.dtek-kem.com.ua/api/oauth/authorize")
        self.assertEqual(call.kwargs["params"]["redirect_uri"], "https://biz.ok.dtek-kem.com.ua/auth/callback")
        for call in self.session.request.call_args_list:
            self.assertTrue(call.args[1].startswith("https://biz.ok.dtek-kem.com.ua/api/"))
            if "site" in call.kwargs["json"]:
                self.assertEqual(call.kwargs["json"]["site"], "kem")
        await client.close()

    async def asyncSetUp(self):
        self.modern_session = NS(request=AsyncMock(), close=AsyncMock(), cookies={})
        self.modern = transport.DTEKAccountClient(self.modern_session)
        encoded = base64.urlsafe_b64encode(json.dumps({"user_data_id": "synthetic-user"}).encode()).decode()
        self.modern._token = "header." + encoded + ".signature"
        self.modern._ensure_token = AsyncMock()
        self.modern._accounts = {NUMBER, OTHER}
        self.modern.api_issue_handler = Mock()

        def redirect(method, url, **kwargs):
            p = kwargs["params"]
            return response(302, headers={"location": p["redirect_uri"] + "?" + urlencode({"state": p["state"], "code": "synthetic-code"})})
        self.modern_session.request.side_effect = redirect
        self.session = NS(request=AsyncMock(), close=AsyncMock())
        self.client = mod.AccountGroupClient(self.modern, self.session)
        self.login = response(data={"status": "success", "user": {"token": "private-legacy-token"}})

    async def asyncTearDown(self):
        await self.client.close()
        await self.modern.close()

    async def test_handoff_uses_separate_session_and_exact_account_before_groups(self):
        self.session.request.side_effect = [self.login, info(), group_response()]
        self.assertEqual(await self.client.read_groups(NUMBER), ("GPV3.1",))
        authorize = self.modern_session.request.call_args
        self.assertEqual(authorize.args[1], "https://auth.my.dtek-oem.com.ua/api/oauth/authorize")
        self.assertFalse(authorize.kwargs["allow_redirects"])
        state = json.loads(authorize.kwargs["params"]["state"])
        self.assertEqual(state["account"], NUMBER)
        self.assertTrue(state["nonce"])
        login, profile, groups = self.session.request.call_args_list
        self.assertEqual(login.kwargs["json"]["action"], "token")
        self.assertEqual(login.kwargs["json"]["site"], "oem")
        self.assertEqual(base64.b64decode(login.kwargs["headers"]["Authorization"][6:]).decode(), NUMBER + ":synthetic-code")
        for call in (login, profile, groups):
            self.assertNotIn("X-Id-Token", call.kwargs["headers"])
            self.assertNotIn(self.modern._token, str(call))
            self.assertFalse(call.kwargs["allow_redirects"])
        self.assertEqual(groups.kwargs["json"]["eic"], EIC)
        self.assertEqual(groups.kwargs["json"]["account"], NUMBER)
        self.modern.api_issue_handler.assert_not_called()

    async def test_foreign_account_or_missing_eic_never_requests_groups(self):
        for customer in ({"account": OTHER, "eic": EIC}, {"account": NUMBER}, {"account": NUMBER, "eic": "invalid"}):
            self.client._tokens.clear()
            self.session.request.reset_mock()
            self.session.request.side_effect = [self.login, response(data={"customer": customer})]
            with self.subTest(customer=customer), self.assertRaises(account.AccountAPIChanged):
                await self.client.read_groups(NUMBER)
            self.assertEqual(self.session.request.await_count, 2)

    async def test_expired_legacy_token_renews_once_without_modern_reauthentication(self):
        self.client._tokens[NUMBER] = "expired"
        self.session.request.side_effect = [response(401), self.login, info(), group_response()]
        self.assertEqual(await self.client.read_groups(NUMBER), ("GPV3.1",))
        self.assertEqual(self.modern_session.request.await_count, 1)
        self.client._tokens[NUMBER] = "expired"
        self.session.request.side_effect = [response(401), self.login, response(401)]
        with self.assertRaises(account.AccountAuthError):
            await self.client.read_groups(NUMBER)
        self.assertNotIn(NUMBER, self.client._tokens)

    async def test_endpoint_retirement_is_optional_and_transient_errors_are_not_retirement(self):
        for status in (404, 410, 429, 403, 500, 302):
            self.client._tokens[NUMBER] = "test-token"
            self.session.request.side_effect = None
            self.session.request.return_value = response(status)
            with self.subTest(status=status), self.assertRaises(account.AccountError) as caught:
                await self.client.read_groups(NUMBER)
            self.assertEqual(isinstance(caught.exception, account.AccountAPIChanged), status in (404, 410))
            if status in (404, 410): self.assertEqual(caught.exception.interface, "account_groups")
            self.modern.api_issue_handler.assert_not_called()

    async def test_redirect_origin_state_and_code_are_validated_before_token_exchange(self):
        for target in ("https://evil.invalid/auth/callback?code=x", "https://ok.dtek-oem.com.ua/auth/callback?code=x&state=other", "/login"):
            self.modern_session.request.side_effect = None
            self.modern_session.request.return_value = response(302, headers={"location": target})
            with self.subTest(target=target), self.assertRaises(account.AccountAuthError):
                await self.client.read_groups(NUMBER)
            self.session.request.assert_not_called()

    async def test_unknown_account_and_missing_modern_authorizer_cannot_authorize(self):
        with self.assertRaises(account.AccountError):
            await self.modern.authorize_account_groups("572000000099")
        self.modern_session.request.assert_not_called()
        for provider in ("dtek_kem", "dtek_krem", "dtek_dnem"):
            self.assertIsNone(mod.create_account_group_provider(NS(spec=regions.ACCOUNT_PROVIDER_SPECS[provider])))

    async def test_tokens_are_separate_for_each_account_and_cleared_on_close(self):
        self.session.request.side_effect = [self.login, info(), group_response(), self.login, info(OTHER), group_response("GPV5.1")]
        self.assertEqual(await self.client.read_groups(NUMBER), ("GPV3.1",))
        self.assertEqual(await self.client.read_groups(OTHER), ("GPV5.1",))
        self.assertEqual(self.modern_session.request.await_count, 2)
        self.assertEqual(set(self.client._tokens), {NUMBER, OTHER})
        await self.client.close()
        self.assertEqual(self.client._tokens, {})

    async def test_foreign_eic_is_rejected_even_if_the_account_number_matches(self):
        self.modern._linked_profiles = {NUMBER: {"eic": ["00TEST0000000002"]}}
        self.session.request.side_effect = [self.login, info()]
        with self.assertRaises(account.AccountError):
            await self.client.read_groups(NUMBER)
        self.assertEqual(self.session.request.await_count, 2)

    async def test_network_and_malformed_response_errors_never_expose_private_data(self):
        self.client._tokens[NUMBER] = "private-token"
        for failure in (TimeoutError("private URL"), OSError("private token")):
            self.session.request.side_effect = failure
            with self.assertRaises(account.AccountError) as caught:
                await self.client.read_groups(NUMBER)
            self.assertNotIn("private", str(caught.exception))
        self.session.request.side_effect = None
        self.session.request.return_value = NS(status_code=200, json=Mock(side_effect=ValueError("private response")))
        with self.assertRaises(account.AccountAPIChanged) as caught:
            await self.client.read_groups(NUMBER)
        self.assertNotIn("private", str(caught.exception))

    async def test_optional_handoff_404_does_not_report_main_login_broken(self):
        self.modern_session.request.side_effect = None
        self.modern_session.request.return_value = response(404)
        with self.assertRaises(account.AccountAPIChanged) as caught:
            await self.client.read_groups(NUMBER)
        self.assertEqual(caught.exception.interface, "account_groups")
        self.modern.api_issue_handler.assert_not_called()
        self.session.request.assert_not_called()

    async def test_application_level_failure_cannot_pin_an_expired_token_forever(self):
        self.client._tokens[NUMBER] = "expired"
        self.session.request.side_effect = [response(data={"status": "fail", "error": "session rejected"})]
        with self.assertRaises(account.AccountError):
            await self.client.read_groups(NUMBER)
        self.assertNotIn(NUMBER, self.client._tokens)
        self.session.request.assert_awaited_once()
        self.session.request.side_effect = [self.login, info(), group_response()]
        self.assertEqual(await self.client.read_groups(NUMBER), ("GPV3.1",))
