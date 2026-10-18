"""Native cabinet management and persistent API warnings against real HA."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import test_account_runtime as cabinet
import test_ha_runtime as runtime
from component_loader import load_component_module

if runtime.HAS_HA:
    from homeassistant.helpers import entity_registry as er, issue_registry as ir
    from homeassistant.helpers.update_coordinator import UpdateFailed

    account = load_component_module("account")
    links = load_component_module("account_link_flow")
    health = load_component_module("account_api_health")
    selection = load_component_module("account_flow")

NUMBER = "572000000001"


@unittest.skipUnless(runtime.HAS_HA, "Requires real Home Assistant runtime")
class LinkFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await cabinet.AccountRuntimeTests.asyncSetUp(self)
        await ir.async_load(self.hass)
        self.entry.title = "Synthetic account"
        self.entry.domain = "dtek_monitor"
        self.entry.data["accounts"] = {}
        self.hass.config_entries.async_get_entry.return_value = self.entry
        self.hass.config_entries.async_entries.return_value = [self.entry]
        self.candidate = account.PersonalAccount(NUMBER, "м. Приклад, вул. Тестова 1")
        self.account_client.list_accounts.return_value = ()
        self.account_client.lookup_account = AsyncMock(return_value=self.candidate)
        self.account_client.link_account = AsyncMock()
        self.account_client.unlink_account = AsyncMock()
        for module in [links, selection]:
            factory = patch.object(
                module, "create_account_provider", return_value=self.account_client
            )
            factory.start()
            self.addCleanup(factory.stop)
        self.options = runtime.flow.DTEKMonitorOptionsFlow(self.entry)
        self.options.hass = self.hass

    async def review_link(self):
        result = await self.options.async_step_account_link({"account_number": NUMBER})
        self.assertEqual(result["step_id"], "account_link_confirm")
        self.assertEqual(
            result["description_placeholders"]["address"], self.candidate.address
        )
        return result

    async def review_unlink(self):
        self.account_client.list_accounts.return_value = (self.candidate,)
        result = await self.options.async_step_account_unlink(
            {"account_number": NUMBER}
        )
        self.assertEqual(result["step_id"], "account_unlink_confirm")
        return result

    async def test_opening_menus_and_lookup_never_writes(self):
        result = await self.options.async_step_init()
        self.assertEqual(result["step_id"], "account_management")
        await self.options.async_step_account_link()
        self.account_client.authenticate.assert_not_awaited()
        await self.review_link()
        result = await self.options.async_step_account_link_confirm({"confirm": False})
        self.assertEqual(result["errors"], {"base": "link_confirm_required"})
        self.account_client.link_account.assert_not_awaited()
        self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_confirm_once_then_choose_local_monitoring_separately(self):
        await self.review_link()
        result = await self.options.async_step_account_link_confirm({"confirm": True})
        self.assertEqual(result["step_id"], "account_linked")
        self.account_client.link_account.assert_awaited_once()
        self.hass.config_entries.async_update_entry.assert_not_called()
        repeat = await self.options.async_step_account_link_confirm({"confirm": True})
        self.assertEqual(repeat["reason"], "link_review_expired")
        self.account_client.link_account.assert_awaited_once()
        self.account_client.list_accounts.return_value = (self.candidate,)
        result = await self.options.async_step_account_linked({})
        self.assertEqual(result["step_id"], "account_selection")
        result = await self.options.async_step_account_selection({"accounts": [NUMBER]})
        self.assertEqual(result["type"], "create_entry")
        updated = self.hass.config_entries.async_update_entry.call_args.kwargs["data"]
        self.assertEqual(updated["accounts"], {NUMBER: self.candidate.address})

    async def test_deselect_all_preserves_credentials_options_and_upstream_links(self):
        self.entry.data["accounts"] = {NUMBER: self.candidate.address}
        self.entry.options = {"energy_reference": {"private": "synthetic"}}
        self.account_client.list_accounts.return_value = (self.candidate,)
        result = await self.options.async_step_account_selection({"accounts": []})
        self.assertEqual(result["data"], self.entry.options)
        updated = self.hass.config_entries.async_update_entry.call_args.kwargs["data"]
        self.assertEqual(updated, {**self.entry.data, "accounts": {}})
        self.account_client.unlink_account.assert_not_awaited()

    async def test_empty_authenticated_cabinet_can_be_saved_and_updated(self):
        self.hass.config_entries.async_entries.return_value = []
        result = await self.flow.async_step_account_login(
            {"username": "+380500000001", "password": "synthetic"}
        )
        self.assertEqual(
            result["menu_options"], ["account_link", "account_finish_empty"]
        )
        result = await self.flow.async_step_account_finish_empty()
        self.assertEqual(result["description"], "account_connected_empty")
        self.assertEqual(result["data"]["accounts"], {})
        self.assertEqual(result["data"]["account_subject"], "subject")
        self.assertEqual(await self.account_coord._async_update_data(), {})
        self.account_client.read_account.assert_not_awaited()

    async def test_expired_review_cannot_write(self):
        with patch.object(links, "monotonic", return_value=100):
            await self.review_link()
        with patch.object(links, "monotonic", return_value=401):
            result = await self.options.async_step_account_link_confirm(
                {"confirm": True}
            )
        self.assertEqual(result["reason"], "link_review_expired")
        self.account_client.link_account.assert_not_awaited()

    async def test_removed_entry_changed_credentials_or_identity_cannot_write(self):
        for failure in ["entry", "password", "subject"]:
            self.options = runtime.flow.DTEKMonitorOptionsFlow(self.entry)
            self.options.hass = self.hass
            self.account_client.authenticate.return_value = "subject"
            self.hass.config_entries.async_get_entry.return_value = self.entry
            await self.review_link()
            if failure == "entry":
                self.hass.config_entries.async_get_entry.return_value = None
            elif failure == "password":
                self.entry.data = {**self.entry.data, "password": "changed"}
            else:
                self.account_client.authenticate.return_value = "another-subject"
            result = await self.options.async_step_account_link_confirm(
                {"confirm": True}
            )
            self.assertEqual(result["reason"], "link_session_changed")
        self.account_client.link_account.assert_not_awaited()

    async def test_monitored_account_or_entities_waiting_for_reload_block_unlink(self):
        self.account_client.list_accounts.return_value = (self.candidate,)
        self.entry.data["accounts"] = {NUMBER: self.candidate.address}
        result = await self.options.async_step_account_unlink(
            {"account_number": NUMBER}
        )
        self.assertEqual(result["errors"], {"base": "link_in_use"})
        self.entry.data["accounts"] = {}
        registry = er.async_get(self.hass)
        entity = registry.async_get_or_create(
            "sensor",
            "dtek_monitor",
            f"account:{account.account_key('dtek_oem', NUMBER)}:reading",
        )
        result = await self.options.async_step_account_unlink(
            {"account_number": NUMBER}
        )
        self.assertEqual(result["errors"], {"base": "link_in_use"})
        registry.async_remove(entity.entity_id)
        await self.review_unlink()
        # Selection in another form invalidates the pending confirmation.
        self.entry.data["accounts"] = {NUMBER: self.candidate.address}
        result = await self.options.async_step_account_unlink_confirm({"confirm": True})
        self.assertEqual(result["reason"], "link_in_use")
        self.account_client.unlink_account.assert_not_awaited()

    async def test_unlink_list_is_stable_and_confirmation_writes_only_once(self):
        await self.review_unlink()
        self.account_client.list_accounts.return_value = ()
        await self.options.async_step_account_unlink()
        self.account_client.list_accounts.assert_awaited_once()
        result = await self.options.async_step_account_unlink_confirm({"confirm": True})
        self.assertEqual(result["reason"], "link_unlinked")
        self.account_client.unlink_account.assert_awaited_once()
        self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_failed_write_has_no_automatic_replay_or_local_changes(self):
        await self.review_link()
        self.account_client.link_account.side_effect = links.AccountLinkError(
            "link_uncertain"
        )
        result = await self.options.async_step_account_link_confirm({"confirm": True})
        self.assertEqual(result["reason"], "link_uncertain")
        await self.options.async_step_account_link_confirm({"confirm": True})
        self.account_client.link_account.assert_awaited_once()
        self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_concurrent_confirmation_is_consumed_before_network_wait(self):
        await self.review_link()
        entered, release = asyncio.Event(), asyncio.Event()

        async def write(*_):
            entered.set()
            await release.wait()

        self.account_client.link_account.side_effect = write
        first = asyncio.create_task(
            self.options.async_step_account_link_confirm({"confirm": True})
        )
        await entered.wait()
        repeat = await self.options.async_step_account_link_confirm({"confirm": True})
        release.set()
        self.assertEqual(repeat["reason"], "link_review_expired")
        self.assertEqual((await first)["step_id"], "account_linked")
        self.account_client.link_account.assert_awaited_once()

    async def test_api_repairs_deduplicate_and_clear_only_matching_operation(self):
        issue = account.AccountAPIChanged("cabinet", "HTTP 410")
        self.account_client.list_accounts.side_effect = issue
        for _ in range(2):
            with self.assertRaises(UpdateFailed):
                await self.account_coord._async_update_data()
        registry = ir.async_get(self.hass)
        key = ("dtek_monitor", "account_api_dtek_oem_cabinet")
        self.assertIn(key, registry.issues)
        self.assertEqual(len(registry.issues), 1)
        warning = registry.issues[key]
        self.assertEqual(warning.learn_more_url, health.ISSUES_URL)
        self.assertEqual(
            warning.translation_placeholders["releases_url"], health.RELEASES_URL
        )
        self.assertNotIn(NUMBER, str(warning.translation_placeholders))
        health.report_api_issue(
            self.hass, "dtek_oem", account.AccountAPIChanged("link_add", "HTTP 410")
        )
        health.report_api_issue(
            self.hass,
            "dtek_oem",
            account.AccountAPIChanged("meter_history", "HTTP 410"),
        )
        self.account_client.list_accounts.side_effect = None
        await self.account_coord._async_update_data()
        self.assertNotIn(key, registry.issues)
        self.assertIn(
            ("dtek_monitor", "account_api_dtek_oem_link_add"), registry.issues
        )
        self.assertIn(
            ("dtek_monitor", "account_api_dtek_oem_meter_history"), registry.issues
        )
        await self.review_link()
        await self.options.async_step_account_link_confirm({"confirm": True})
        self.assertNotIn(
            ("dtek_monitor", "account_api_dtek_oem_link_add"), registry.issues
        )
        self.account_client.api_recovered_handler("meter_history")
        self.assertEqual(len(registry.issues), 0)

    async def test_api_change_in_native_form_has_actionable_error_and_repair(self):
        self.account_client.list_accounts.side_effect = account.AccountAPIChanged(
            "cabinet", "response_format"
        )
        result = await self.options.async_step_account_link({"account_number": NUMBER})
        self.assertEqual(result["errors"], {"base": "account_api_changed"})
        self.assertEqual(len(ir.async_get(self.hass).issues), 1)
        self.account_client.link_account.assert_not_awaited()
