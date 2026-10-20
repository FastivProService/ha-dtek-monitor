"""Verified Kyiv legacy lookup; fictional identifiers and no cabinet writes."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from component_loader import load_component_module

links = load_component_module("account_links")
transport = load_component_module("account_client")
regions = load_component_module("account_providers")
account = load_component_module("account")
OLD = "10000000001"
NEW = "452000000001"
SPEC = regions.ACCOUNT_PROVIDER_SPECS["dtek_kem"]


def payload(*numbers):
    return {"status": "success", "error": None, "data": {
        "errors": [], "resultItems": [{"inclLvl1Items": [
            {"account": number, "legacyAccount": "opaque", "inclLvl2Items": []}
            for number in numbers
        ]}],
    }}


class LegacyTests(unittest.IsolatedAsyncioTestCase):
    def test_legacy_is_lookup_only_and_operator_specific(self):
        self.assertEqual(links.lookup_number(" " + OLD + " ", SPEC), OLD)
        self.assertEqual(links.lookup_number(NEW, SPEC), NEW)
        for value, spec in ((OLD, regions.ACCOUNT_PROVIDER_SPECS["dtek_oem"]),
                            (None, SPEC), ("１" * 11, SPEC), (OLD + "?x", SPEC)):
            with self.assertRaises(links.AccountLinkError):
                links.lookup_number(value, spec)
        with self.assertRaises(links.AccountLinkError):
            links.household_number(OLD, SPEC)

    def test_strict_bounded_hierarchy_deduplicates_without_picking_one(self):
        self.assertEqual(links.parse_legacy_numbers(payload(NEW, NEW), SPEC), (NEW,))
        self.assertEqual(len(links.parse_legacy_numbers(payload(NEW, "452000000002"), SPEC)), 2)
        invalid = [None, {}, payload(), payload("572000000001"), payload(OLD),
                   payload(*([NEW] * 51))]
        for key, value in (("errors", ["private"]), ("resultItems", {})):
            changed = payload(NEW)
            changed["data"][key] = value
            invalid.append(changed)
        invalid.append({**payload(NEW), "error": "private"})
        invalid.append({**payload(NEW), "data": {"errors": [], "resultItems": [{}]}})
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(links.AccountLinkError):
                links.parse_legacy_numbers(value, SPEC)

    async def test_legacy_read_uses_verified_route_then_exact_canonical_lookup(self):
        response = SimpleNamespace(status_code=200, json=lambda: payload(NEW), headers={})
        session = SimpleNamespace(cookies={}, request=AsyncMock(return_value=response), close=AsyncMock())
        client = transport.DTEKAccountClient(session, spec=SPEC)
        client._ensure_token = AsyncMock()
        candidate = account.PersonalAccount(NEW, "Example address")
        client.lookup_account = AsyncMock(return_value=candidate)
        try:
            self.assertEqual(await client.lookup_legacy_accounts(OLD), (candidate,))
            call = session.request.call_args
            self.assertEqual(call.args[:2], ("POST", "https://my.dtek-kem.com.ua/api/billing/joint/info/get_client_info"))
            self.assertEqual(call.kwargs["json"], {"legacy": OLD})
            client.lookup_account.assert_awaited_once_with(NEW)
            session.request.assert_awaited_once()
            session.request.reset_mock()
            with self.assertRaises(links.AccountLinkError):
                await client.lookup_legacy_accounts(NEW)
            session.request.assert_not_awaited()
        finally:
            await client.close()


import test_account_link_runtime as runtime


@unittest.skipUnless(runtime.runtime.HAS_HA, "Requires Home Assistant")
class LegacyFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.LinkFlowTests.asyncSetUp(self)
        self.entry.data["provider_id"] = "dtek_kem"
        self.candidate = account.PersonalAccount(NEW, "Example address")
        self.account_client.lookup_legacy_accounts = AsyncMock(return_value=(self.candidate,))
        self.account_client.lookup_account.return_value = self.candidate

    async def test_single_alias_requires_review_of_full_current_identity(self):
        form = await self.options.async_step_account_link()
        self.assertEqual(form["step_id"], "account_link_legacy")
        result = await self.options.async_step_account_link_legacy({"account_number": OLD})
        self.assertEqual(result["step_id"], "account_link_confirm")
        self.assertEqual(result["description_placeholders"], {"number": NEW, "address": "Example address"})
        self.account_client.link_account.assert_not_awaited()
        await self.options.async_step_account_link_confirm({"confirm": True})
        self.assertEqual(self.account_client.link_account.call_args.args[0], self.candidate)

    async def test_ambiguous_result_needs_choice_and_never_accepts_arbitrary_account(self):
        other = account.PersonalAccount("452000000002", "Second address")
        self.account_client.lookup_legacy_accounts.return_value = (self.candidate, other)
        result = await self.options.async_step_account_link({"account_number": OLD})
        self.assertEqual(result["step_id"], "account_legacy_choice")
        self.assertIsNone(self.options._link_review)
        result = await self.options.async_step_account_legacy_choice({"account_number": other.number})
        self.assertEqual(result["description_placeholders"]["number"], other.number)
        self.account_client.link_account.assert_not_awaited()
        await self.options.async_step_account_link({"account_number": OLD})
        result = await self.options.async_step_account_legacy_choice({"account_number": "452000000099"})
        self.assertEqual(result["reason"], "link_changed")

    async def test_already_linked_alias_and_expired_choice_do_not_write(self):
        self.account_client.list_accounts.return_value = (self.candidate,)
        result = await self.options.async_step_account_link({"account_number": OLD})
        self.assertEqual(result["errors"]["base"], "link_already_linked")
        self.options._legacy_choices = ({NEW: self.candidate}, -1000)
        result = await self.options.async_step_account_legacy_choice({"account_number": NEW})
        self.assertEqual(result["reason"], "link_review_expired")
        self.account_client.link_account.assert_not_awaited()
