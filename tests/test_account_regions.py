"""Regional cabinet contracts using synthetic sessions, never live accounts."""

import asyncio
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from component_loader import load_component_module
from test_account import response, token

account = load_component_module("account")
regions = load_component_module("account_providers")
transport = load_component_module("account_client")
access = load_component_module("account_access")
links = load_component_module("account_links")
public = load_component_module("providers")

# Deliberately fictional modern numbers, including DNEM's two-digit prefix.
NUMBERS = {
    "dtek_oem": "572000000001",
    "dtek_kem": "452000000001",
    "dtek_krem": "842000000001",
    "dtek_dnem": "120000000001",
}


def enabled_specs():
    """Registry copy for tests that simulate changing regional settings."""
    return {
        key: replace(spec, enabled=True)
        for key, spec in regions.ACCOUNT_PROVIDER_SPECS.items()
    }


def session():
    return SimpleNamespace(cookies={}, request=AsyncMock(), close=AsyncMock())


class RegionalContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_four_cabinets_enabled_with_explicit_experimental_regions(self):
        for provider in NUMBERS:
            self.assertTrue(public.PROVIDERS[provider].enabled)
            self.assertEqual(
                regions.account_provider_supported(provider), True
            )
            self.assertEqual(
                regions.get_account_provider_spec(provider).experimental,
                provider in ("dtek_krem", "dtek_dnem"),
            )
        with patch.object(transport, "AsyncSession") as factory:
            for provider in [
                "dtek_dem",
                "unknown",
            ]:
                with (
                    self.subTest(provider=provider),
                    self.assertRaises(account.AccountError),
                ):
                    transport.create_account_provider(provider)
            factory.assert_not_called()

    async def test_factory_passes_selected_spec_and_owns_separate_sessions(self):
        sessions = [session() for _ in NUMBERS]
        with (
            patch.object(transport, "AsyncSession", side_effect=sessions) as factory,
        ):
            clients = [transport.create_account_provider(p) for p in NUMBERS]
        self.assertEqual(factory.call_count, 4)
        for call in factory.call_args_list:
            self.assertFalse(call.kwargs["allow_redirects"])
        for provider, client in zip(NUMBERS, clients):
            self.assertEqual(client.spec.provider_id, provider)
            with self.assertRaises(AttributeError):
                client.spec = regions.ACCOUNT_PROVIDER_SPECS["dtek_oem"]
            await client.close()
        for item in sessions:
            item.close.assert_awaited_once()

    def test_modern_numbers_reject_other_regions_business_legacy_and_unicode(self):
        for provider, number in NUMBERS.items():
            spec = regions.ACCOUNT_PROVIDER_SPECS[provider]
            self.assertEqual(links.household_number(" " + number + " ", spec), number)
            invalid = [n for p, n in NUMBERS.items() if p != provider] + [
                None,
                number[:-1],
                number + "0",
                number + "?x=1",
                "old/123",
                "５" + number[1:],
                "571000000001",
                "451000000001",
                "841000000001",
                "110000000001",
            ]
            for value in invalid:
                with (
                    self.subTest(provider=provider, value=value),
                    self.assertRaises(links.AccountLinkError),
                ):
                    links.household_number(value, spec)

    async def test_login_and_refresh_keep_oauth_and_billing_on_selected_origins(self):
        for provider in NUMBERS:
            with self.subTest(provider=provider):
                spec = regions.ACCOUNT_PROVIDER_SPECS[provider]
                # Distinct ID verifies it is consumed from the spec, not a constant.
                spec = replace(spec, oauth_client_id="synthetic-" + provider)
                http = session()
                client = transport.DTEKAccountClient(http, spec=spec)

                async def request(method, url, *, spec=spec, **kwargs):
                    if url.endswith("/authorize"):
                        params = kwargs["params"]
                        self.assertEqual(params["client_id"], spec.oauth_client_id)
                        self.assertEqual(params["redirect_uri"], spec.callback)
                        return response(
                            302,
                            headers={
                                "location": spec.callback
                                + "?code=synthetic&state="
                                + params["state"]
                            },
                        )
                    if url.endswith("/userinfo"):
                        return response(data={"sub": "same-subject"})
                    if url.endswith("/api/userData/api/login"):
                        return response(data={"xIdToken": token()})
                    return response()

                http.request.side_effect = request
                await client.authenticate("+380500000001", "synthetic-password")
                calls = http.request.await_args_list
                self.assertEqual(len(calls), 6)
                for index, call in enumerate(calls):
                    origin = spec.auth_origin if index < 3 else spec.cabinet_origin
                    self.assertTrue(call.args[1].startswith(origin + "/"))
                    self.assertEqual(call.kwargs["headers"]["Origin"], origin)
                    self.assertEqual(call.kwargs["headers"]["Referer"], origin + "/")
                self.assertEqual(calls[3].kwargs["data"]["redirect_uri"], spec.callback)
                self.assertEqual(
                    calls[3].kwargs["data"]["client_id"], spec.oauth_client_id
                )

                http.cookies["XSRF-TOKEN"] = provider + "%20csrf"
                client._token_expires = 0
                http.request.reset_mock()
                http.request.side_effect = [
                    response(401),
                    response(),
                    response(data={"xIdToken": token()}),
                ]
                await client._ensure_token()
                calls = http.request.await_args_list
                self.assertEqual(
                    calls[1].kwargs["data"],
                    {"grant_type": "refresh_token", "client_id": spec.oauth_client_id},
                )
                self.assertTrue(
                    all(c.args[1].startswith(spec.cabinet_origin + "/") for c in calls)
                )
                self.assertEqual(
                    calls[1].kwargs["headers"]["X-CSRF"], provider + " csrf"
                )
                await client.close()

    async def test_callback_from_another_region_is_rejected_even_with_correct_state(
        self,
    ):
        for provider in NUMBERS:
            spec = regions.ACCOUNT_PROVIDER_SPECS[provider]
            other = next(
                s for p, s in regions.ACCOUNT_PROVIDER_SPECS.items() if p != provider
            )
            http = session()
            client = transport.DTEKAccountClient(http, spec=spec)

            async def request(method, url, *, other=other, **kwargs):
                if url.endswith("/authorize"):
                    return response(
                        302,
                        headers={
                            "location": other.callback
                            + "?code=x&state="
                            + kwargs["params"]["state"]
                        },
                    )
                return response()

            http.request.side_effect = request
            with self.assertRaises(account.AccountAuthError):
                await client.authenticate("+380500000001", "synthetic-password")
            self.assertEqual(http.request.await_count, 3)
            self.assertIsNone(client.subject)
            await client.close()

    async def test_account_lookup_and_mutations_validate_region_before_network(self):
        for provider, number in NUMBERS.items():
            spec = regions.ACCOUNT_PROVIDER_SPECS[provider]
            http = session()
            client = transport.DTEKAccountClient(http, spec=spec)
            client._ensure_token = AsyncMock()
            client._token = provider + "-token"
            candidate = account.PersonalAccount(number, "Приклад, вул. Тестова 1")
            http.request.return_value = response(
                data={
                    "status": "success",
                    "data": [{"account": number, "address": candidate.address}],
                }
            )
            self.assertEqual(await client.lookup_account(number), candidate)
            call = http.request.await_args
            self.assertEqual(
                call.args[1],
                spec.cabinet_origin + "/api/billing/joint/info/get_accounts",
            )
            self.assertEqual(call.kwargs["params"], {"account": number})
            self.assertEqual(call.kwargs["headers"]["X-Id-Token"], client._token)

            http.request.reset_mock()
            foreign = next(n for p, n in NUMBERS.items() if p != provider)
            wrong = account.PersonalAccount(foreign, "Another region")
            for operation, args in (
                (client.lookup_account, (foreign,)),
                (client.link_account, (wrong, Mock())),
                (client.unlink_account, (wrong, Mock())),
            ):
                with self.assertRaises(links.AccountLinkError):
                    await operation(*args)
            http.request.assert_not_awaited()

            client.list_accounts = AsyncMock(return_value=())
            client.lookup_account = AsyncMock(return_value=candidate)
            http.request.return_value = response(data={"status": "success"})
            await client.link_account(candidate, Mock())
            self.assertEqual(
                http.request.await_args.args[:2],
                ("POST", spec.cabinet_origin + "/api/billing/joint/objects/add"),
            )
            client.list_accounts.return_value = (candidate,)
            http.request.return_value = response(204)
            await client.unlink_account(candidate, Mock())
            self.assertEqual(
                http.request.await_args.args[:2],
                ("DELETE", spec.cabinet_origin + "/api/billing/joint/objects/remove"),
            )
            self.assertEqual(http.request.await_count, 2)
            await client.close()

    async def test_parallel_clients_do_not_share_tokens_membership_or_failures(self):
        clients = []
        for provider, number in NUMBERS.items():
            http = session()
            client = transport.DTEKAccountClient(
                http, spec=regions.ACCOUNT_PROVIDER_SPECS[provider]
            )
            client.subject = "same-subject"
            client._token = provider + "-token"
            client._token_expires = time.monotonic() + 900
            http.request.return_value = response(
                data={
                    "status": "success",
                    "data": {
                        "data": [
                            {
                                "isLegal": False,
                                "accountNumber": number,
                                "address": "Synthetic address",
                            }
                        ],
                        "totalPages": 1,
                    },
                }
            )
            clients.append(client)
        results = await asyncio.gather(*(c.list_accounts() for c in clients))
        for client, records in zip(clients, results):
            self.assertEqual(client._accounts, {NUMBERS[client.spec.provider_id]})
            self.assertEqual(records[0].number, NUMBERS[client.spec.provider_id])
            call = client._session.request.await_args
            self.assertTrue(call.args[1].startswith(client.spec.cabinet_origin + "/"))
            self.assertEqual(call.kwargs["headers"]["X-Id-Token"], client._token)
            await client._request("GET", transport.BILLING + "/example")
            self.assertEqual(
                client._session.request.await_args.kwargs["headers"]["X-Id-Token"],
                "Bearer " + client._token,
            )
        clients[0]._session.request.return_value = response(401)
        clients[0]._token_expires = 0
        with self.assertRaises(account.AccountAuthError):
            await clients[0].list_accounts()
        for client in clients[1:]:
            self.assertTrue(await client.list_accounts())
        await asyncio.gather(*(c.close() for c in clients))

    async def test_sms_registration_and_recovery_stay_with_selected_operator(self):
        for provider in NUMBERS:
            spec = regions.ACCOUNT_PROVIDER_SPECS[provider]
            for kind in ("registration", "password-recovery"):
                http = session()
                client = access.AccountAccessClient(http, spec=spec)
                key = "registration_id" if kind == "registration" else "recovery_id"
                http.request.side_effect = [
                    response(),
                    response(data={key: "synthetic-challenge"}),
                ]
                await client.start(kind, "+380500000001")
                http.request.side_effect = None
                http.request.return_value = response()
                await client.verify("0123")
                if kind == "registration":
                    await client.profile("Тест", "Приклад", "")
                await client.finalize("EXample123", "EXample123", consent=True)
                for call in http.request.await_args_list:
                    self.assertTrue(call.args[1].startswith(spec.auth_origin + "/"))
                    self.assertEqual(call.kwargs["headers"]["Origin"], spec.auth_origin)
                self.assertEqual(
                    sum(
                        c.args[1].endswith("/finalize")
                        for c in http.request.await_args_list
                    ),
                    1,
                )
                await client.close()
