"""Synthetic cabinet links and API compatibility boundaries; no live writes."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from component_loader import load_component_module

account = load_component_module("account")
client_module = load_component_module("account_client")
links = load_component_module("account_links")
NUMBER = "572000000001"
CANDIDATE = account.PersonalAccount(NUMBER, "Приклад, вул. Тестова 1, кв. 2")


def response(status=200, data=None):
    return SimpleNamespace(status_code=status, json=lambda: data, headers={})


class LinksTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = SimpleNamespace(
            request=AsyncMock(), close=AsyncMock(), cookies={}
        )
        self.client = client_module.DTEKAccountClient(self.session)
        self.client._ensure_token = AsyncMock()
        self.client.list_accounts = AsyncMock(return_value=())
        self.client.lookup_account = AsyncMock(return_value=CANDIDATE)
        self.authorize = Mock()

    async def asyncTearDown(self):
        await self.client.close()

    async def error(self, code, coro):
        with self.assertRaises(links.AccountLinkError) as caught:
            await coro
        self.assertEqual(caught.exception.code, code)

    def test_lookup_requires_one_exact_matching_household_address(self):
        valid = {
            "status": "success",
            "data": [{"account": NUMBER, "address": CANDIDATE.address}],
            "error": None,
        }
        self.assertEqual(links.parse_lookup(valid, NUMBER), CANDIDATE)
        for payload in [
            None,
            [],
            {},
            {"status": "error"},
            {"status": "success", "data": []},
            {"status": "success", "data": valid["data"] * 2},
            {
                "status": "success",
                "data": [{"account": "572000000002", "address": "Elsewhere"}],
            },
            {"status": "success", "data": [{"account": NUMBER, "address": ""}]},
        ]:
            with (
                self.subTest(payload=payload),
                self.assertRaises(links.AccountLinkError),
            ):
                links.parse_lookup(payload, NUMBER)
        for number in [
            "old/123",
            "571000000001",
            "572000001",
            "572000000001?x=1",
            "５７２000000001",
            None,
        ]:
            with self.assertRaises(links.AccountLinkError):
                links.household_number(number)

    async def test_link_rechecks_address_and_permissions_before_single_post(self):
        self.session.request.return_value = response(
            data={"status": "success", "data": {}}
        )
        await self.client.link_account(CANDIDATE, self.authorize)
        self.authorize.assert_called_once()
        self.session.request.assert_awaited_once()
        call = self.session.request.call_args
        self.assertEqual(
            call.args[:2],
            ("POST", "https://my.dtek-oem.com.ua/api/billing/joint/objects/add"),
        )
        self.assertEqual(call.kwargs["json"], {"data": {"account": NUMBER}})

    async def test_lookup_address_change_or_already_linked_never_posts(self):
        self.client.lookup_account.return_value = account.PersonalAccount(
            NUMBER, "Changed"
        )
        await self.error(
            "link_changed", self.client.link_account(CANDIDATE, self.authorize)
        )
        self.client.list_accounts.return_value = (CANDIDATE,)
        await self.error(
            "link_already_linked", self.client.link_account(CANDIDATE, self.authorize)
        )
        self.session.request.assert_not_awaited()

    async def test_revoked_authorization_never_reaches_mutation(self):
        self.authorize.side_effect = links.AccountLinkError("link_in_use")
        await self.error(
            "link_in_use", self.client.link_account(CANDIDATE, self.authorize)
        )
        self.session.request.assert_not_awaited()

    async def test_unlink_only_current_link_and_204_confirms_success(self):
        await self.error(
            "link_not_linked", self.client.unlink_account(CANDIDATE, self.authorize)
        )
        self.client.list_accounts.return_value = (CANDIDATE,)
        self.session.request.return_value = response(204)
        await self.client.unlink_account(CANDIDATE, self.authorize)
        call = self.session.request.call_args
        self.assertEqual(call.args[0], "DELETE")
        self.assertEqual(call.kwargs["params"], {"account": NUMBER})
        self.session.request.assert_awaited_once()

    async def test_mutation_errors_are_not_retried_and_do_not_expose_payload(self):
        for status, code in [
            (401, "link_rejected"),
            (404, "link_rejected"),
            (429, "link_rate_limit"),
            (500, "link_uncertain"),
        ]:
            self.session.request.reset_mock()
            self.session.request.return_value = response(
                status, {"private": "sensitive information"}
            )
            await self.error(code, self.client.link_account(CANDIDATE, self.authorize))
            self.session.request.assert_awaited_once()
        self.session.request.side_effect = TimeoutError("private URL")
        await self.error(
            "link_uncertain", self.client.link_account(CANDIDATE, self.authorize)
        )

    async def test_unexpected_success_body_is_uncertain_not_confirmed(self):
        for body in [None, {}, {"status": "success", "error": "private"}]:
            self.session.request.return_value = response(data=body)
            await self.error(
                "link_uncertain", self.client.link_account(CANDIDATE, self.authorize)
            )
        self.session.request.return_value = response(200)
        self.client.list_accounts.return_value = (CANDIDATE,)
        await self.error(
            "link_uncertain", self.client.unlink_account(CANDIDATE, self.authorize)
        )


class APICompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = SimpleNamespace(
            request=AsyncMock(), close=AsyncMock(), cookies={}
        )
        self.client = client_module.DTEKAccountClient(self.session)
        self.client._ensure_token = AsyncMock()
        self.handler = Mock()
        self.client.api_issue_handler = self.handler

    async def asyncTearDown(self):
        await self.client.close()

    async def test_missing_fixed_list_route_and_gone_gateway_have_specific_signal(self):
        for path, status, family in [
            (links.LINKS + "/get_advanced", 404, "cabinet"),
            (links.LINKS + "/add", 410, "link_add"),
            (
                client_module.BILLING + "/meters/household/572000000001/readings",
                410,
                "meter_submission",
            ),
        ]:
            self.session.request.return_value = response(status)
            with self.assertRaises(account.AccountAPIChanged) as error:
                await self.client._request("GET", path)
            self.assertEqual(error.exception.interface, family)
            self.assertEqual(error.exception.reason, f"HTTP {status}")
            self.assertNotIn(NUMBER, str(error.exception))
        self.assertEqual(self.handler.call_count, 3)

    async def test_lookup_404_auth_errors_rate_limits_and_timeouts_are_not_retirement(
        self,
    ):
        for status in [401, 403, 404, 429, 500, 503]:
            self.session.request.return_value = response(status)
            await self.client._request("GET", "/api/billing/joint/info/get_accounts")
        for status in [403, 429, 500]:
            self.session.request.return_value = response(status)
            await self.client._request("GET", links.LINKS + "/get_advanced")
        self.session.request.side_effect = TimeoutError()
        with self.assertRaises(account.AccountError) as error:
            await self.client._request("GET", links.LINKS + "/get_advanced")
        self.assertNotIsInstance(error.exception, account.AccountAPIChanged)
        self.handler.assert_not_called()

    async def test_changed_list_contract_is_distinct_from_business_error_and_empty_cabinet(
        self,
    ):
        self.session.request.return_value = response(
            data={"status": "success", "data": {"new_contract": []}}
        )
        with self.assertRaises(account.AccountAPIChanged):
            await self.client.list_accounts()
        self.handler.reset_mock()
        self.session.request.return_value = response(
            data={"status": "error", "error": "temporary backend failure"}
        )
        with self.assertRaises(account.AccountError) as error:
            await self.client.list_accounts()
        self.assertNotIsInstance(error.exception, account.AccountAPIChanged)
        self.handler.assert_not_called()
        self.session.request.return_value = response(
            data={"status": "success", "data": {"data": [], "totalPages": 0}}
        )
        self.assertEqual(await self.client.list_accounts(), ())

    async def test_non_json_list_or_changed_row_contract_warns_instead_of_empty_accounts(
        self,
    ):
        broken = SimpleNamespace(
            status_code=200,
            headers={},
            json=Mock(side_effect=ValueError("private html")),
        )
        self.session.request.return_value = broken
        with self.assertRaises(account.AccountAPIChanged):
            await self.client.list_accounts()
        for row in [
            {},
            {"accountNumber": NUMBER, "address": "Example"},
            {"isLegal": False, "accountNumber": NUMBER},
        ]:
            self.session.request.return_value = response(
                data={"status": "success", "data": {"totalPages": 1, "data": [row]}}
            )
            with self.assertRaises(account.AccountAPIChanged):
                await self.client.list_accounts()

    async def test_read_recovery_cannot_clear_another_route_or_submission(self):
        recovered = Mock()
        self.client.api_recovered_handler = recovered
        self.session.request.return_value = response(data={"items": []})
        await self.client._get(
            client_module.BILLING + "/meters/household/572000000001/history"
        )
        recovered.assert_called_once_with("meter_history")
        recovered.reset_mock()
        self.session.request.return_value = response(500)
        with self.assertRaises(account.AccountError):
            await self.client._get(
                client_module.BILLING + "/meters/household/572000000001/history"
            )
        recovered.assert_not_called()

    async def test_valid_empty_list_clears_list_warning_but_business_error_does_not(
        self,
    ):
        recovered = Mock()
        self.client.api_recovered_handler = recovered
        self.session.request.return_value = response(
            data={"status": "error", "error": "unavailable"}
        )
        with self.assertRaises(account.AccountError):
            await self.client.list_accounts()
        recovered.assert_not_called()
        self.session.request.return_value = response(
            data={"status": "success", "data": {"data": [], "totalPages": 0}}
        )
        self.assertEqual(await self.client.list_accounts(), ())
        recovered.assert_called_once_with("cabinet")

    async def test_token_renewal_clears_login_warning_only_after_valid_token(self):
        from test_account import token

        recovered = Mock()
        self.client.api_recovered_handler = recovered
        self.session.request.return_value = response(data={"xIdToken": "invalid"})
        with self.assertRaises(account.AccountError):
            await self.client._issue_token()
        recovered.assert_not_called()
        self.session.request.return_value = response(data={"xIdToken": token()})
        await self.client._issue_token()
        recovered.assert_called_once_with("login")
