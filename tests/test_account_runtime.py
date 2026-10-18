"""Cabinet setup, reauth and isolation using real Home Assistant classes."""

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_ha_runtime as runtime
from component_loader import load_component_module

if runtime.HAS_HA:
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.exceptions import ConfigEntryAuthFailed
    from homeassistant.data_entry_flow import AbortFlow
    from homeassistant.const import Platform

    account = load_component_module("account")
    flow_module = load_component_module("account_flow")
    coordinator_module = load_component_module("account_coordinator")
    sensors = load_component_module("account_sensor")
    setup_module = load_component_module("__init__")


@unittest.skipUnless(runtime.HAS_HA, "Requires real Home Assistant runtime")
class AccountRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.RuntimeTests.asyncSetUp(self)
        self.entry.data = {
            "entry_kind": "account",
            "provider_id": "dtek_oem",
            "username": "+380500000001",
            "password": "synthetic-test-password",
            "account_subject": "subject",
            "accounts": {
                "100001": "м. Приклад, вул. Тестова буд. 1, кв.7",
                "100002": "м. Приклад, вул. Тестова буд. 2, кв.8",
            },
        }
        self.entry.add_update_listener = Mock(return_value=lambda: None)
        self.entry.state = ConfigEntryState.SETUP_IN_PROGRESS
        self.hass.config_entries = Mock()
        self.hass.config_entries.async_forward_entry_setups = AsyncMock()
        self.hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        self.hass.config_entries.flow.async_init = AsyncMock(
            return_value={"type": "form"}
        )
        self.hass.config_entries.flow.async_progress_by_handler.return_value = []
        self.hass.config_entries.async_entry_for_domain_unique_id.return_value = None
        self.hass.config_entries.async_entries.return_value = []
        self.snapshot = account.AccountSnapshot(
            "Example",
            2030,
            (account.Reading("GENERAL", Decimal(123), date(2026, 9, 30)),),
            Decimal(-12),
            "2026-09",
            datetime.now(timezone.utc),
        )
        self.account_client = SimpleNamespace(
            authenticate=AsyncMock(return_value="subject"),
            list_accounts=AsyncMock(
                return_value=tuple(
                    account.PersonalAccount(n, a)
                    for n, a in self.entry.data["accounts"].items()
                )
            ),
            read_account=AsyncMock(return_value=self.snapshot),
            close=AsyncMock(),
        )
        self.account_coord = coordinator_module.AccountCoordinator(
            self.hass, self.entry, self.account_client
        )
        self.flow = runtime.flow.DTEKMonitorConfigFlow()
        self.flow.hass = self.hass
        self.flow.context = {"source": "user"}

    async def test_setup_and_unload_use_only_account_sensor_platform(self):
        with patch.object(
            setup_module, "create_account_provider", return_value=self.account_client
        ), patch.object(setup_module, "create_provider") as public:
            self.assertTrue(await setup_module.async_setup_entry(self.hass, self.entry))
            public.assert_not_called()
            self.hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
                self.entry, [Platform.SENSOR]
            )
            self.assertTrue(
                await setup_module.async_unload_entry(self.hass, self.entry)
            )
            self.account_client.close.assert_awaited_once()

    async def test_account_auth_failure_closes_client_without_touching_address_provider(
        self,
    ):
        self.account_client.authenticate.side_effect = account.AccountAuthError("test")
        with patch.object(
            setup_module, "create_account_provider", return_value=self.account_client
        ), patch.object(setup_module, "create_provider") as public:
            with self.assertRaises(ConfigEntryAuthFailed):
                await setup_module.async_setup_entry(self.hass, self.entry)
            public.assert_not_called()
            self.account_client.close.assert_awaited_once()

    async def test_one_account_failure_is_unavailable_without_erasing_other_account(
        self,
    ):
        self.account_client.read_account.side_effect = [
            account.AccountError("test"),
            self.snapshot,
        ]
        await self.account_coord.async_refresh()
        self.assertTrue(self.account_coord.last_update_success)
        failed = sensors.AccountSensor(
            self.account_coord, self.entry, "100001", "account_consumption"
        )
        healthy = sensors.AccountSensor(
            self.account_coord, self.entry, "100002", "account_consumption"
        )
        self.assertFalse(failed.available)
        self.assertTrue(healthy.available)
        self.assertEqual(healthy.native_value, -12)
        self.assertEqual(healthy.extra_state_attributes, {"period": "2026-09"})
        self.assertIsNone(healthy.state_class)

    async def test_removed_account_is_never_requested(self):
        self.account_client.list_accounts.return_value = (
            account.PersonalAccount("100002", "address"),
        )
        await self.account_coord.async_refresh()
        self.account_client.read_account.assert_awaited_once_with("100002")
        self.assertIsNone(self.account_coord.data["100001"])

    async def test_rate_limit_stops_processing_further_accounts(self):
        self.account_client.read_account.side_effect = account.AccountRateLimitError(
            "test"
        )
        await self.account_coord.async_refresh()
        self.assertFalse(self.account_coord.last_update_success)
        self.assertEqual(self.account_client.read_account.await_count, 1)

    async def test_late_scales_are_added_once_without_duplicate_entities(self):
        self.account_coord.async_set_updated_data({"100001": None, "100002": None})
        discover = sensors.account_sensors(self.account_coord, self.entry)
        initial = discover()
        self.assertEqual(len(initial), 8)
        self.assertFalse(discover())
        self.account_coord.async_set_updated_data(
            {"100001": self.snapshot, "100002": None}
        )
        new = discover()
        self.assertEqual(len(new), 2)
        self.assertFalse(discover())
        reading = next(e for e in new if e.key == "account_reading")
        self.assertEqual(reading.native_value, 123)
        self.assertIsNone(reading.state_class)
        self.assertNotIn("100001", reading.unique_id)
        self.assertNotIn("outage", reading.unique_id)
        self.assertEqual(reading.extra_state_attributes["reading_date"], "2026-09-30")

    async def test_operator_then_connection_menu_has_explicit_capability_gate(self):
        result = await self.flow.async_step_user({"provider_id": "dtek_oem"})
        self.assertEqual(result["menu_options"], ["address", "account_login"])
        result = await self.flow.async_step_user({"provider_id": "dtek_kem"})
        self.assertEqual(result["menu_options"], ["address"])
        self.assertEqual(
            (await self.flow.async_step_account_login())["reason"],
            "account_not_supported",
        )

    async def test_account_flow_creates_separate_entry_and_rejects_unknown_selection(
        self,
    ):
        with patch.object(
            flow_module, "create_account_provider", return_value=self.account_client
        ):
            result = await self.flow.async_step_account_login(
                {"username": "+380500000001", "password": "synthetic-test-password"}
            )
        self.assertEqual(result["step_id"], "accounts")
        self.account_client.close.assert_awaited_once()
        for selection in [[], ["999999"], [{}], None]:
            result = await self.flow.async_step_accounts({"accounts": selection})
            self.assertEqual(result["errors"], {"base": "select_accounts"})
        result = await self.flow.async_step_accounts({"accounts": ["100002"]})
        self.assertEqual(result["data"]["entry_kind"], "account")
        self.assertEqual(list(result["data"]["accounts"]), ["100002"])
        self.assertNotIn("city", result["data"])
        self.assertTrue(self.flow.unique_id.startswith("account:"))
        self.assertFalse(self.flow._account_credentials)

    async def test_duplicate_account_closes_transient_session(self):
        self.hass.config_entries.async_entry_for_domain_unique_id.return_value = (
            SimpleNamespace(source="user")
        )
        with patch.object(
            flow_module, "create_account_provider", return_value=self.account_client
        ):
            with self.assertRaises(AbortFlow):
                await self.flow.async_step_account_login(
                    {"username": "+380500000001", "password": "synthetic-test-password"}
                )
        self.account_client.close.assert_awaited_once()

    async def test_same_personal_account_from_another_login_is_not_duplicated(self):
        with patch.object(
            flow_module, "create_account_provider", return_value=self.account_client
        ):
            await self.flow.async_step_account_login(
                {"username": "+380500000001", "password": "synthetic-test-password"}
            )
        self.hass.config_entries.async_entries.return_value = [self.entry]
        result = await self.flow.async_step_accounts({"accounts": ["100001"]})
        self.assertEqual(result["errors"], {"base": "accounts_already_configured"})
        self.assertTrue(self.flow._account_credentials)

    async def test_address_hint_normalization_requires_a_unique_match(self):
        self.flow._address_suggestion = {"city": "м. Приклад", "street": "вул. Тестова"}
        self.assertEqual(
            self.flow._suggested_default("city", ["Приклад"]), {"default": "Приклад"}
        )
        self.assertEqual(
            self.flow._suggested_default("street", ["вулиця Тестова"]),
            {"default": "вулиця Тестова"},
        )
        self.assertEqual(
            self.flow._suggested_default("city", ["Приклад", "місто Приклад"]), {}
        )

    async def test_bad_credentials_show_safe_error_and_no_password_default(self):
        self.account_client.authenticate.side_effect = account.AccountAuthError(
            "private provider payload"
        )
        with patch.object(
            flow_module, "create_account_provider", return_value=self.account_client
        ):
            result = await self.flow.async_step_account_login(
                {"username": "+380500000001", "password": "synthetic-test-password"}
            )
        self.assertEqual(result["errors"], {"base": "invalid_auth"})
        self.assertNotIn("private provider payload", str(result))
        self.assertNotIn("synthetic-test-password", str(result))
        self.account_client.close.assert_awaited_once()

    async def test_reauth_cannot_switch_identity(self):
        self.flow.context = {"source": "reauth", "entry_id": "test-entry"}
        self.hass.config_entries.async_get_known_entry.return_value = self.entry
        self.account_client.authenticate.return_value = "different-subject"
        with patch.object(
            flow_module, "create_account_provider", return_value=self.account_client
        ):
            result = await self.flow.async_step_account_login(
                {"username": "+380500000002", "password": "synthetic-test-password"}
            )
        self.assertEqual(result["reason"], "account_mismatch")
        self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_address_options_pass_only_building_hints_to_independent_flow(self):
        options = runtime.flow.DTEKMonitorOptionsFlow(self.entry)
        options.hass = self.hass
        result = await options.async_step_account({"address_account": "100001"})
        self.assertEqual(result["reason"], "address_setup_started")
        data = self.hass.config_entries.flow.async_init.call_args.kwargs["data"]
        self.assertEqual(
            data,
            {
                "provider_id": "dtek_oem",
                "suggestion": {
                    "city": "м. Приклад",
                    "street": "вул. Тестова",
                    "house_num": "1",
                },
            },
        )
        for private in ["password", "100001", "кв.7", "subject", "username"]:
            self.assertNotIn(private, str(data))

    async def test_ambiguous_building_format_is_not_silently_truncated(self):
        self.assertEqual(
            flow_module.building_suggestion(
                "м. Приклад, вул. Тестова буд. 1, корпус 2, кв.7"
            ),
            {},
        )

    async def test_import_requires_public_house_confirmation_and_keeps_duplicate_identity(
        self,
    ):
        self.flow.context = {"source": "integration_discovery"}
        provider = SimpleNamespace(
            list_localities=AsyncMock(return_value=["м. Приклад"]),
            list_streets=AsyncMock(return_value=["вул. Тестова"]),
            list_houses=AsyncMock(return_value=["1"]),
            close=AsyncMock(),
        )
        with patch.object(runtime.flow, "create_provider", return_value=provider):
            result = await self.flow.async_step_integration_discovery(
                {
                    "provider_id": "dtek_oem",
                    "suggestion": {
                        "city": "м. Приклад",
                        "street": "вул. Тестова",
                        "house_num": "1",
                    },
                }
            )
            self.assertEqual(result["step_id"], "city")
            await self.flow.async_step_city({"city": "м. Приклад"})
            await self.flow.async_step_street({"street": "вул. Тестова"})
            result = await self.flow.async_step_house({"house_num": "1, кв.7"})
            self.assertEqual(result["errors"], {"house_num": "invalid_house"})
            self.hass.config_entries.async_entry_for_domain_unique_id.return_value = (
                SimpleNamespace(source="user", state=ConfigEntryState.LOADED)
            )
            with self.assertRaises(AbortFlow):
                await self.flow.async_step_house({"house_num": "1"})
            self.assertEqual(self.flow.unique_id, "м. Приклад_вул. Тестова_1")
