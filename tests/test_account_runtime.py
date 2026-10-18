"""Cabinet setup, reauth and isolation using real Home Assistant classes."""

from datetime import date, datetime, timezone
import asyncio
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_ha_runtime as runtime
from component_loader import load_component_module

if runtime.HAS_HA:
    from homeassistant.config_entries import ConfigEntryState, OptionsFlowManager
    from homeassistant.exceptions import ConfigEntryAuthFailed
    from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    from homeassistant.data_entry_flow import AbortFlow
    from homeassistant.const import Platform

    account = load_component_module("account")
    flow_module = load_component_module("account_flow")
    coordinator_module = load_component_module("account_coordinator")
    sensors = load_component_module("account_sensor")
    setup_module = load_component_module("__init__")
    buttons = load_component_module("button")


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
        self.entry.async_start_reauth = Mock()
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
        await er.async_load(self.hass)
        await dr.async_load(self.hass)
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

    async def test_setup_and_unload_use_only_account_platforms(self):
        with patch.object(
            setup_module, "create_account_provider", return_value=self.account_client
        ), patch.object(setup_module, "create_provider") as public:
            self.assertTrue(await setup_module.async_setup_entry(self.hass, self.entry))
            public.assert_not_called()
            self.hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
                self.entry, [Platform.SENSOR, Platform.BUTTON, Platform.CALENDAR]
            )
            self.assertTrue(
                await setup_module.async_unload_entry(self.hass, self.entry)
            )
            self.account_client.close.assert_awaited_once()

    async def test_manual_refresh_buttons_share_limit_and_preserve_old_data_on_failure(
        self,
    ):
        self.entry.runtime_data = self.account_coord
        first, second = [
            buttons.AccountRefreshButton(self.entry, n)
            for n in self.entry.data["accounts"]
        ]
        with patch.object(coordinator_module, "monotonic", return_value=100):
            await first.async_press()
            with self.assertRaises(ServiceValidationError):
                await second.async_press()
        self.assertEqual(self.account_client.read_account.await_count, 2)
        self.account_client.read_account.side_effect = account.AccountError(
            "private error"
        )
        with patch.object(coordinator_module, "monotonic", return_value=161):
            with self.assertRaises(HomeAssistantError) as ctx:
                await second.async_press()
            self.assertEqual(ctx.exception.translation_key, "account_refresh_failed")
            with self.assertRaises(ServiceValidationError):
                await first.async_press()
        self.assertFalse(
            sensors.AccountSensor(
                self.account_coord, self.entry, "100001", "account_consumption"
            ).available
        )
        self.assertEqual(self.account_coord.data["100001"], self.snapshot)

    async def test_simultaneous_manual_refresh_and_scheduled_refresh_cooldown(self):
        with patch.object(coordinator_module, "monotonic", return_value=100):
            results = await asyncio.gather(
                self.account_coord.async_manual_refresh(),
                self.account_coord.async_manual_refresh(),
                return_exceptions=True,
            )
            self.assertEqual(
                sum(isinstance(r, ServiceValidationError) for r in results), 1
            )
        self.assertEqual(self.account_client.list_accounts.await_count, 1)
        with patch.object(coordinator_module, "monotonic", return_value=200):
            await self.account_coord.async_refresh()
            with self.assertRaises(ServiceValidationError):
                await self.account_coord.async_manual_refresh()

    async def test_partial_manual_refresh_reports_failure_without_hiding_healthy_account(
        self,
    ):
        self.account_client.read_account.side_effect = [
            self.snapshot,
            account.AccountError("private"),
        ]
        with self.assertRaises(HomeAssistantError):
            await self.account_coord.async_manual_refresh()
        self.assertTrue(self.account_coord.last_update_success)
        self.assertIsNone(self.account_coord.data["100002"])
        self.assertIsNotNone(self.account_coord.data["100001"])

    async def test_account_selection_updates_data_and_keeps_credentials(self):
        options = runtime.flow.DTEKMonitorOptionsFlow(self.entry)
        options.hass = self.hass
        self.assertEqual(
            (await options.async_step_init())["menu_options"],
            ["account_selection", "account", "account_schedule", "account_reminders"],
        )
        with patch.object(
            flow_module, "create_account_provider", return_value=self.account_client
        ):
            form = await options.async_step_account_selection()
            for selection in [[], ["unknown"], [{}]]:
                form = await options.async_step_account_selection(
                    {"accounts": selection}
                )
                self.assertEqual(form["errors"], {"base": "select_accounts"})
            result = await options.async_step_account_selection(
                {"accounts": ["100002"]}
            )
        self.account_client.list_accounts.assert_awaited_once()
        self.account_client.close.assert_awaited_once()
        self.assertEqual(result["type"], "create_entry")
        updated = self.hass.config_entries.async_update_entry.call_args.kwargs["data"]
        self.assertEqual(
            updated,
            {
                **self.entry.data,
                "accounts": {"100002": self.entry.data["accounts"]["100002"]},
            },
        )
        self.hass.config_entries.flow.async_init.assert_not_awaited()

    async def test_options_menu_runs_through_home_assistant_flow_manager(self):
        manager = OptionsFlowManager(self.hass)
        options = runtime.flow.DTEKMonitorOptionsFlow(self.entry)
        self.hass.config_entries.async_get_known_entry.return_value = self.entry
        with patch.object(
            manager, "async_create_flow", AsyncMock(return_value=options)
        ), patch.object(
            flow_module, "create_account_provider", return_value=self.account_client
        ):
            result = await manager.async_init(self.entry.entry_id)
            self.assertEqual(result["step_id"], "account_menu")
            form = await manager.async_configure(
                result["flow_id"], {"next_step_id": "account_selection"}
            )
            self.assertEqual(form["step_id"], "account_selection")
            manager.async_abort(form["flow_id"])

    async def test_account_selection_rejects_duplicate_and_preserves_submitted_choices(
        self,
    ):
        options = runtime.flow.DTEKMonitorOptionsFlow(self.entry)
        options.hass = self.hass
        self.hass.config_entries.async_entries.return_value = [
            self.entry,
            SimpleNamespace(
                entry_id="other",
                data={**self.entry.data, "accounts": {"100002": "address"}},
            ),
        ]
        with patch.object(
            flow_module, "create_account_provider", return_value=self.account_client
        ):
            result = await options.async_step_account_selection(
                {"accounts": ["100002"]}
            )
        self.assertEqual(result["errors"], {"base": "accounts_already_configured"})
        self.assertEqual(next(iter(result["data_schema"].schema)).default(), ["100002"])
        self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_options_auth_and_network_errors_leave_selection_unchanged_and_close_session(
        self,
    ):
        for error, reason in [
            (account.AccountAuthError("private"), "account_auth_required"),
            (account.AccountError("private"), "account_cannot_connect"),
            (account.AccountRateLimitError("private"), "account_rate_limit"),
        ]:
            with self.subTest(reason=reason):
                options = runtime.flow.DTEKMonitorOptionsFlow(self.entry)
                options.hass = self.hass
                self.account_client.authenticate.side_effect = error
                self.account_client.close.reset_mock()
                with patch.object(
                    flow_module,
                    "create_account_provider",
                    return_value=self.account_client,
                ):
                    result = await options.async_step_account_selection()
                self.assertEqual(result["reason"], reason)
                self.account_client.close.assert_awaited_once()
                self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_cleanup_removes_only_deselected_private_entities(self):
        entities = er.async_get(self.hass)
        keep = account.account_key("dtek_oem", "100001")
        remove = account.account_key("dtek_oem", "100002")
        records = []
        self.entry.pref_disable_new_entities = False
        self.entry.domain = "dtek_monitor"
        self.entry.discovery_keys = {}
        for uid, entry in [
            (f"account:{keep}:account_reading:GENERAL", self.entry),
            (f"account:{remove}:account_reading:GENERAL", self.entry),
            ("public-address:power", self.entry),
            (
                f"account:{remove}:account_reading:GENERAL",
                SimpleNamespace(**{**vars(self.entry), "entry_id": "other-entry"}),
            ),
        ]:
            # Other connection uses another platform to avoid a global registry collision.
            platform = (
                "dtek_monitor" if entry.entry_id == self.entry.entry_id else "other"
            )
            records.append(
                entities.async_get_or_create(
                    "sensor", platform, uid, config_entry=entry
                )
            )
        self.entry.data = {**self.entry.data, "accounts": {"100001": "address"}}
        sensors.remove_unselected_accounts(self.hass, self.entry)
        self.assertIsNotNone(entities.async_get(records[0].entity_id))
        self.assertIsNone(entities.async_get(records[1].entity_id))
        self.assertIsNotNone(entities.async_get(records[2].entity_id))
        self.assertIsNotNone(entities.async_get(records[3].entity_id))

    async def test_cleanup_preserves_selected_shared_and_public_devices(self):
        keep = account.account_key("dtek_oem", "100001")
        remove = account.account_key("dtek_oem", "100002")
        self.entry.data = {**self.entry.data, "accounts": {"100001": "address"}}
        devices = [
            SimpleNamespace(
                id="keep",
                identifiers={("dtek_monitor", f"account:{keep}")},
                config_entries={self.entry.entry_id},
            ),
            SimpleNamespace(
                id="remove",
                identifiers={("dtek_monitor", f"account:{remove}")},
                config_entries={self.entry.entry_id},
            ),
            SimpleNamespace(
                id="shared",
                identifiers={("dtek_monitor", "account:shared")},
                config_entries={self.entry.entry_id, "other"},
            ),
            SimpleNamespace(
                id="public",
                identifiers={("dtek_monitor", "public-address")},
                config_entries={self.entry.entry_id},
            ),
        ]
        registry = dr.async_get(self.hass)
        with patch.object(
            dr, "async_entries_for_config_entry", return_value=devices
        ), patch.object(registry, "async_remove_device") as remove_device:
            sensors.remove_unselected_accounts(self.hass, self.entry)
        remove_device.assert_called_once_with("remove")

    async def test_selection_cannot_switch_subject_or_save_an_empty_linked_list(self):
        for subject, linked, reason in [
            ("other", (), "account_mismatch"),
            ("subject", (), "no_accounts"),
        ]:
            options = runtime.flow.DTEKMonitorOptionsFlow(self.entry)
            options.hass = self.hass
            self.account_client.authenticate.return_value = subject
            self.account_client.list_accounts.return_value = linked
            with patch.object(
                flow_module, "create_account_provider", return_value=self.account_client
            ):
                result = await options.async_step_account_selection()
            self.assertEqual(result["reason"], reason)
            self.hass.config_entries.async_update_entry.assert_not_called()

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
