"""Diagnostic exports must be useful without leaking config or API identities."""

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from component_loader import load_component_module

diagnostics = load_component_module("diagnostics")
NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
PRIVATE = "private-marker-never-export"


def entry(kind="address", **kwargs):
    return NS(
        data={"entry_kind": kind, "provider_id": "dtek_oem", "password": PRIVATE},
        options={"future_option": PRIVATE}, title=PRIVATE, unique_id=PRIVATE,
        entry_id=PRIVATE, version=4, minor_version=1, state=NS(value="loaded"),
        **kwargs,
    )


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def assert_private(self, result, *values):
        encoded = json.dumps(result, allow_nan=False)
        for value in (PRIVATE, *values):
            self.assertNotIn(value, encoded)

    async def test_address_health_and_stale_snapshot_without_private_payloads(self):
        refresh = AsyncMock()
        coord = NS(
            last_update_success=False, last_exception=ValueError(PRIVATE),
            update_interval=timedelta(minutes=10), connection_state="error",
            schedule_state="unpublished", address_success=True,
            notifications_ready=False, consecutive_failures=3,
            last_success=NOW, address_last_success=NOW, schedule_last_success=None,
            address_error=None, schedule_error=PRIVATE, snapshot_storage_error=None,
            schedule_reason=PRIVATE, city=PRIVATE, street=PRIVATE, house=PRIVATE,
            address_data={"schedule_groups": [PRIVATE, PRIVATE], "outage_description": PRIVATE},
            address_works=[NS(description=PRIVATE)], works_incomplete=True,
            schedule=NS(
                groups=[PRIVATE, PRIVATE], intervals=[PRIVATE], coverage=[PRIVATE],
                fetched_at=NOW, source_updated_at=NOW,
                publication=NS(schedule=False, daily=False, weekly=None, group_labels=True),
                address=PRIVATE, revision=PRIVATE, reason=PRIVATE,
            ),
            snapshot=NS(
                checked_at=NOW, coverage_start=NOW, coverage_end=NOW + timedelta(days=1),
                groups=[PRIVATE], windows=[PRIVATE, PRIVATE], snapshot_id=PRIVATE,
                address=PRIVATE, source_updated_at=PRIVATE,
            ),
            provider=NS(diagnostics={
                "transport_errors": 3, "protection_challenges": 2,
                "protection_blocks": 1, "browser_profile": "chrome136",
                "public_retry_in_seconds": 120, "public_consecutive_failures": 2,
                "cookies": PRIVATE, "future_api_field": PRIVATE,
            }, refresh=refresh),
        )
        result = await diagnostics.async_get_config_entry_diagnostics(None, entry(runtime_data=coord))
        self.assertEqual(result["runtime"]["update_interval_seconds"], 600)
        self.assertFalse(result["runtime"]["last_update_success"])
        address = result["address"]
        self.assertTrue(address["address_success"])
        self.assertFalse(address["notifications_ready"])
        self.assertEqual(address["schedule_state"], "unpublished")
        self.assertEqual(address["group_count"], 2)
        self.assertTrue(address["errors"]["schedule_error"])
        self.assertFalse(address["errors"]["address_error"])
        self.assertEqual(address["snapshot"]["checked_at"], NOW.isoformat())
        self.assertEqual(address["snapshot"]["interval_count"], 2)
        self.assertIsNone(address["schedule"]["publication"]["weekly"])
        self.assertEqual(address["transport"]["browser_profile"], "chrome136")
        self.assertEqual(address["transport"]["public_retry_in_seconds"], 120)
        self.assertEqual(address["transport"]["public_consecutive_failures"], 2)
        self.assertEqual(address["repairs"], {"interval_count": 1, "incomplete": True})
        refresh.assert_not_called()
        self.assert_private(result)

    async def test_account_ids_keys_profiles_readings_and_options_are_not_exported(self):
        config = entry("account", runtime_data=NS(
            data={
                "123456789012": NS(fetched_at=NOW, readings=[PRIVATE], profile={
                    "address": PRIVATE, "eic": PRIVATE, "account": "123456789012",
                }, consumption=9876543, model=PRIVATE),
                "999999999999": None,
                "unselected-private-account": NS(fetched_at=NOW, profile=PRIVATE),
            },
            buildings=NS(states={"999999999999": "address_confirmation"}),
            last_update_success=True, update_interval=timedelta(hours=6),
            client=NS(_token=PRIVATE, _accounts={PRIVATE: PRIVATE}),
            energy=NS(_data={PRIVATE: PRIVATE}), submissions=NS(_data={PRIVATE: PRIVATE}),
        ))
        config.data.update({
            "accounts": {"123456789012": PRIVATE, "999999999999": PRIVATE},
            "username": PRIVATE, "account_subject": PRIVATE,
        })
        config.options["account_schedules"] = {"123456789012": {
            "address_entry_id": PRIVATE, "groups": [PRIVATE, PRIVATE],
        }}
        result = await diagnostics.async_get_config_entry_diagnostics(None, config)
        self.assertEqual(result["accounts"]["selected_count"], 2)
        first, second = result["accounts"]["items"]
        self.assertTrue(first["cached_data_present"])
        self.assertTrue(first["building_link_configured"])
        self.assertEqual(first["selected_group_count"], 2)
        self.assertEqual(first["reading_zone_count"], 1)
        self.assertFalse(second["cached_data_present"])
        self.assertEqual(second["building_match_state"], "address_confirmation")
        self.assert_private(result, "123456789012", "999999999999", "9876543", "unselected-private-account")

    async def test_failed_setup_and_unloaded_entries_need_no_runtime(self):
        for kind in ("address", "account"):
            for state in ("setup_retry", "not_loaded", "migration_error"):
                with self.subTest(kind=kind, state=state):
                    config = entry(kind)
                    config.state.value = state
                    result = await diagnostics.async_get_config_entry_diagnostics(None, config)
                    self.assertEqual(result["entry"]["state"], state)
                    self.assertEqual(result["runtime"], {"available": False})
                    self.assert_private(result)

    async def test_empty_account_selection_and_legacy_address_defaults(self):
        result = await diagnostics.async_get_config_entry_diagnostics(None, entry("account"))
        self.assertEqual(result["accounts"], {"selected_count": 0, "items": []})
        config = entry()
        config.data = {}
        result = await diagnostics.async_get_config_entry_diagnostics(None, config)
        self.assertEqual(result["entry"]["kind"], "address")
        self.assertEqual(result["entry"]["provider_id"], "dtek_oem")

    async def test_failed_setup_can_export_shared_operator_pause_without_runtime(self):
        hass = NS(data={"dtek_monitor_public_requests": {
            "dtek_oem": NS(retry_in=120, failures=2, private_field=PRIVATE),
            "dtek_krem": NS(retry_in=900, failures=3),
        }})
        result = await diagnostics.async_get_config_entry_diagnostics(hass, entry())
        self.assertEqual(result["public_requests"], {
            "retry_in_seconds": 120, "consecutive_failures": 2,
        })
        self.assertFalse(result["runtime"]["available"])
        self.assert_private(result)

    async def test_unexpected_values_in_technical_fields_cannot_leak_text(self):
        config = entry(runtime_data=NS(
            connection_state=PRIVATE, schedule_state=PRIVATE,
            address_success=PRIVATE, notifications_ready=PRIVATE,
            consecutive_failures=PRIVATE, last_success=PRIVATE,
            last_update_success=PRIVATE, update_interval=PRIVATE,
            provider=NS(diagnostics={"transport_errors": PRIVATE, "browser_profile": PRIVATE}),
        ))
        config.data["provider_id"] = PRIVATE
        config.state.value = PRIVATE
        result = await diagnostics.async_get_config_entry_diagnostics(None, config)
        self.assertIsNone(result["entry"]["provider_id"])
        self.assertIsNone(result["address"]["connection_state"])
        self.assertIsNone(result["runtime"]["last_update_success"])
        self.assert_private(result)
