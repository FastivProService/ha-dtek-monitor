"""HA setup, account management and SMS isolation across operator regions."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_account_access_runtime as access_tests
import test_account_link_runtime as link_tests
import test_account_runtime as cabinet
import test_ha_runtime as runtime
from component_loader import load_component_module
from test_account_regions import NUMBERS, enabled_specs, regions, response, session

if runtime.HAS_HA:
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import issue_registry as ir

    account = load_component_module("account")
    flows = load_component_module("account_flow")
    links = load_component_module("account_link_flow")
    health = load_component_module("account_api_health")
    access_flow = load_component_module("account_access_flow")
    access = load_component_module("account_access")
    sensors = load_component_module("account_sensor")
    schedules = load_component_module("account_schedule")


@unittest.skipUnless(runtime.HAS_HA, "Requires real Home Assistant runtime")
class RegionalFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await cabinet.AccountRuntimeTests.asyncSetUp(self)
        self.addAsyncCleanup(self.flow._close_access)

    async def test_same_phone_and_subject_create_distinct_regional_entries(self):
        ids = []
        titles = []
        with patch.object(
            flows, "create_account_provider", return_value=self.account_client
        ) as factory:
            for provider in NUMBERS:
                flow = runtime.flow.DTEKMonitorConfigFlow()
                flow.hass = self.hass
                flow.context = {"source": "user"}
                menu = await flow.async_step_user({"provider_id": provider})
                self.assertIn("account_access", menu["menu_options"])
                login = await flow.async_step_account_login()
                origin = regions.ACCOUNT_PROVIDER_SPECS[provider].auth_origin
                self.assertEqual(
                    login["description_placeholders"],
                    {
                        "signup_url": origin + "/signup",
                        "recovery_url": origin + "/recovery",
                    },
                )
                await flow.async_step_account_login(
                    {"username": "+380500000001", "password": "synthetic"}
                )
                factory.assert_called_with(provider)
                result = await flow.async_step_accounts({"accounts": ["100001"]})
                self.assertEqual(result["data"]["provider_id"], provider)
                self.assertEqual(result["data"]["account_subject"], "subject")
                ids.append(flow.unique_id)
                titles.append(result["title"])
                self.assertNotIn("+380500000001", result["title"])
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(len(set(titles)), 4)
        self.assertEqual(
            ids[0], "account:" + account.account_key("dtek_oem", "subject")
        )

    async def test_sensors_and_personal_schedule_open_the_same_regional_cabinet(self):
        for provider, number in NUMBERS.items():
            self.entry.data["provider_id"] = provider
            sensor = sensors.AccountSensor(
                self.account_coord, self.entry, number, "account_updated"
            )
            schedule = schedules.AccountScheduleEntity(
                self.entry, number, "account_outage_schedule"
            )
            expected = regions.ACCOUNT_PROVIDER_SPECS[provider].cabinet_origin + "/"
            self.assertEqual(sensor.device_info["configuration_url"], expected)
            self.assertEqual(schedule.device_info["configuration_url"], expected)
            self.assertEqual(
                sensor.device_info["identifiers"], schedule.device_info["identifiers"]
            )

    async def test_empty_cabinets_on_same_phone_have_distinct_regional_titles(self):
        titles = []
        for provider in NUMBERS:
            self.flow._provider_id = provider
            self.flow._account_credentials = {
                "username": "+380500000001",
                "password": "synthetic",
                "account_subject": "same-subject",
            }
            result = await self.flow.async_step_account_finish_empty()
            titles.append(result["title"])
            self.assertNotIn("+380500000001", result["title"])
            self.assertEqual(result["data"]["provider_id"], provider)
        self.assertEqual(len(set(titles)), 4)

    async def test_region_change_clears_old_selection_identity_and_credentials(self):
        with patch.object(
            flows, "create_account_provider", return_value=self.account_client
        ):
            await self.flow.async_step_account_login(
                {"username": "+380500000001", "password": "synthetic"}
            )
        self.assertTrue(self.flow.unique_id)
        self.flow._link_identity = ("dtek_oem", "private")
        self.flow._link_review = ("link", "candidate", 123)
        await self.flow.async_step_user({"provider_id": "dtek_kem"})
        self.assertFalse(self.flow._account_credentials)
        self.assertFalse(self.flow._personal_accounts)
        self.assertIsNone(self.flow.unique_id)
        self.assertIsNone(self.flow._link_identity)
        self.assertIsNone(self.flow._link_review)
        self.assertEqual(
            (await self.flow.async_step_account_finish_empty())["reason"],
            "link_session_changed",
        )

    async def test_reauth_cannot_apply_other_region_credentials(self):
        self.flow.context = {"source": "reauth", "entry_id": self.entry.entry_id}
        self.hass.config_entries.async_get_known_entry.return_value = self.entry
        self.flow._provider_id = "dtek_kem"
        with patch.object(flows, "create_account_provider") as factory:
            result = await self.flow.async_step_account_login(
                {"username": "+380500000001", "password": "synthetic"}
            )
        self.assertEqual(result["reason"], "account_mismatch")
        factory.assert_not_called()
        result = await self.flow.async_step_account_recover(
            {"username": "+380500000001"}
        )
        self.assertEqual(result["reason"], "account_mismatch")
        self.hass.config_entries.async_update_entry.assert_not_called()


@unittest.skipUnless(runtime.HAS_HA, "Requires real Home Assistant runtime")
class RegionalLinkFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await link_tests.LinkFlowTests.asyncSetUp(self)
        rollout = patch.object(regions, "ACCOUNT_PROVIDER_SPECS", enabled_specs())
        rollout.start()
        self.addCleanup(rollout.stop)
        self.entry.data["provider_id"] = "dtek_kem"
        self.number = NUMBERS["dtek_kem"]
        self.candidate = account.PersonalAccount(
            self.number, "м. Приклад, вул. Тестова 1"
        )
        self.account_client.lookup_account.return_value = self.candidate

    async def test_lookup_and_error_use_selected_region_instead_of_odesa(self):
        result = await self.options.async_step_account_link()
        self.assertEqual(
            result["description_placeholders"],
            {"account_length": "12", "account_prefixes": "452"},
        )
        result = await self.options.async_step_account_link(
            {"account_number": NUMBERS["dtek_oem"]}
        )
        self.assertEqual(result["errors"]["base"], "link_invalid_number")
        self.account_client.authenticate.assert_not_awaited()
        error = account.AccountAPIChanged("link_lookup", "HTTP 410")
        self.account_client.lookup_account.side_effect = error
        result = await self.options.async_step_account_link(
            {"account_number": self.number}
        )
        self.assertEqual(result["errors"]["base"], "account_api_changed")
        registry = ir.async_get(self.hass)
        self.assertIsNotNone(
            registry.async_get_issue("dtek_monitor", "account_api_dtek_kem_link_lookup")
        )
        self.assertIsNone(
            registry.async_get_issue("dtek_monitor", "account_api_dtek_oem_link_lookup")
        )
        health.report_api_issue(self.hass, "dtek_oem", error)
        self.account_client.lookup_account.side_effect = None
        result = await self.options.async_step_account_link(
            {"account_number": self.number}
        )
        self.assertEqual(result["step_id"], "account_link_confirm")
        self.assertIsNone(
            registry.async_get_issue("dtek_monitor", "account_api_dtek_kem_link_lookup")
        )
        self.assertIsNotNone(
            registry.async_get_issue("dtek_monitor", "account_api_dtek_oem_link_lookup")
        )

    async def test_unlink_checks_only_selected_regions_monitors_and_entities(self):
        self.account_client.list_accounts.return_value = (self.candidate,)
        foreign = SimpleNamespace(
            data={
                **self.entry.data,
                "provider_id": "dtek_oem",
                "accounts": {self.number: "Synthetic collision"},
            }
        )
        self.hass.config_entries.async_entries.return_value = [self.entry, foreign]
        registry = er.async_get(self.hass)
        registry.async_get_or_create(
            "sensor",
            "dtek_monitor",
            f"account:{account.account_key('dtek_oem', self.number)}:reading",
        )
        result = await self.options.async_step_account_unlink(
            {"account_number": self.number}
        )
        self.assertEqual(result["step_id"], "account_unlink_confirm")
        # A local monitor appearing while confirmation is open must block the write.
        registry.async_get_or_create(
            "sensor",
            "dtek_monitor",
            f"account:{account.account_key('dtek_kem', self.number)}:reading",
        )
        result = await self.options.async_step_account_unlink_confirm({"confirm": True})
        self.assertEqual(result["reason"], "link_in_use")
        self.account_client.unlink_account.assert_not_awaited()

    async def test_change_region_after_review_cannot_write(self):
        await self.options.async_step_account_link({"account_number": self.number})
        self.entry.data["provider_id"] = "dtek_oem"
        result = await self.options.async_step_account_link_confirm({"confirm": True})
        self.assertEqual(result["reason"], "link_session_changed")
        self.account_client.link_account.assert_not_awaited()


@unittest.skipUnless(runtime.HAS_HA, "Requires real Home Assistant runtime")
class RegionalAccessFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await access_tests.AccessFlowTests.asyncSetUp(self)
        self.addAsyncCleanup(self.flow._close_access)
        rollout = patch.object(regions, "ACCOUNT_PROVIDER_SPECS", enabled_specs())
        rollout.start()
        self.addCleanup(rollout.stop)

    async def test_sms_start_uses_regional_client_and_terms_link(self):
        self.flow._provider_id = "dtek_krem"
        spec = regions.ACCOUNT_PROVIDER_SPECS["dtek_krem"]
        http = session()
        client = access.AccountAccessClient(http, spec=spec)
        http.request.side_effect = [
            response(),
            response(data={"registration_id": "synthetic"}),
        ]
        with patch.object(
            access_flow, "AccountAccessClient", return_value=client
        ) as factory:
            result = await self.flow.async_step_account_register(
                {"username": "+380500000001"}
            )
        self.assertEqual(result["step_id"], "account_sms")
        factory.assert_called_once_with(spec=spec)
        http.request.side_effect = None
        http.request.return_value = response()
        await self.flow.async_step_account_sms({"verification_code": "0123"})
        result = await self.flow.async_step_account_profile(
            {"first_name": "Тест", "last_name": "Приклад"}
        )
        self.assertEqual(
            result["description_placeholders"]["signup_url"],
            spec.auth_origin + "/signup",
        )
        self.assertTrue(
            all(
                c.args[1].startswith(spec.auth_origin + "/")
                for c in http.request.await_args_list
            )
        )

    async def test_region_change_expires_sms_profile_and_password_steps(self):
        for stage in ("code", "profile", "password"):
            await self.flow._close_access()
            client = access.AccountAccessClient(session())
            client.kind = "registration"
            client.phase = stage
            client._challenge = "synthetic"
            self.flow._access_client = client
            self.flow._provider_id = "dtek_kem"
            method = {
                "code": self.flow.async_step_account_sms,
                "profile": self.flow.async_step_account_profile,
                "password": self.flow.async_step_account_registration_password,
            }[stage]
            result = await method(
                {
                    "verification_code": "0123",
                    "accept_terms": True,
                    "password": "EXample123",
                    "confirm_password": "EXample123",
                }
            )
            self.assertEqual(result["reason"], "access_expired")
            client._session.request.assert_not_awaited()
            client._session.close.assert_awaited_once()
            self.assertFalse(client._challenge)

    async def test_region_selector_closes_pending_challenge(self):
        await access_tests.AccessFlowTests.start(self)
        await self.flow.async_step_user({"provider_id": "dtek_dnem"})
        self.assertIsNone(self.flow._access_client)
        self.assertIsNone(self.flow._access_timer)
        self.assertFalse(self.access._challenge)
        self.session.close.assert_awaited_once()
