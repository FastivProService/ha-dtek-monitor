"""Private group selection never narrows the public building/bot calendars."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import test_ha_runtime as runtime
from component_loader import load_component_module

if runtime.HAS_HA:
    from homeassistant.config_entries import ConfigEntryState, ConfigEntryChange
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    mod = load_component_module("account_schedule")
    contract = load_component_module("provider")


@unittest.skipUnless(runtime.HAS_HA, "Requires Home Assistant")
class AccountScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.RuntimeTests.asyncSetUp(self)
        self.entry.title = "Test building"
        self.entry.state = ConfigEntryState.LOADED
        self.entry.runtime_data = self.coord
        self.personal = SimpleNamespace(
            entry_id="private-entry",
            options={"unrelated": 123},
            data={
                "entry_kind": "account",
                "provider_id": "dtek_oem",
                "accounts": {"100001": "Test apartment", "100002": "Second apartment"},
            },
        )
        self.hass.config_entries = Mock()
        self.hass.config_entries.async_entries.return_value = [
            self.entry,
            self.personal,
        ]
        await self.coord.async_refresh()
        now = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        self.coord.address_data.update(
            schedule_groups=["GPV3.1", "GPV5.1"], group_labels_visible=True
        )
        self.coord.data.update(schedule_groups=["GPV3.1", "GPV5.1"])
        self.coord.schedule = replace(
            self.coord.schedule,
            groups=(contract.Group("GPV3.1", "3.1"), contract.Group("GPV5.1", "5.1")),
            intervals=tuple(
                contract.Interval(g, "outage", start, end, "daily")
                for g in ("GPV3.1", "GPV5.1")
            ),
            coverage=tuple(
                contract.Coverage(g, kind, start, end)
                for g in ("GPV3.1", "GPV5.1")
                for kind in ("outage", "possible")
            ),
        )
        self.start, self.end = start, end
        self.calendar = mod.AccountScheduleCalendar(self.personal, "100001", "outage")
        self.calendar.hass = self.hass
        self.sensor = mod.AccountGroupSensor(self.personal, "100001")
        self.sensor.hass = self.hass
        self.options = runtime.flow.DTEKMonitorOptionsFlow(self.personal)
        self.options.hass = self.hass

    def link(self, groups=None):
        self.personal.options[mod.CONF_ACCOUNT_SCHEDULES] = {
            "100001": {
                "address_entry_id": self.entry.entry_id,
                "groups": ["GPV3.1"] if groups is None else groups,
                "selection_source": "user",
            }
        }

    async def test_automatic_selection_requires_exactly_one_verified_building_group(
        self,
    ):
        self.assertFalse(self.calendar.available)
        self.assertIsNone(self.sensor.native_value)
        self.link([])
        self.assertEqual(
            self.sensor.extra_state_attributes["selection_status"], "not_selected"
        )
        self.assertFalse(self.calendar.available)

        self.coord.address_data["schedule_groups"] = ["GPV3.1"]
        self.assertEqual(self.sensor.native_value, "GPV3.1")
        self.assertEqual(
            self.sensor.extra_state_attributes["selection_source"],
            "building_single_group",
        )
        self.assertTrue(self.calendar.available)
        self.coord.address_data["schedule_groups"] = ["GPV3.1", "GPV5.1"]
        self.assertIsNone(self.sensor.native_value)
        self.assertFalse(self.calendar.available)
        self.coord.address_data["schedule_groups"] = []
        self.assertFalse(self.calendar.available)

    async def test_single_group_flow_is_automatic_but_existing_manual_choice_is_kept(
        self,
    ):
        self.coord.address_data["schedule_groups"] = ["GPV3.1"]
        result = await self.choose_address()
        link = result["data"][mod.CONF_ACCOUNT_SCHEDULES]["100001"]
        self.assertEqual(link["groups"], [])
        self.assertEqual(link["selection_source"], "automatic")
        self.personal.options = result["data"]
        result = await self.choose_address()
        self.assertEqual(result["step_id"], "account_schedule_groups")
        result = await self.options.async_step_account_schedule_groups(
            {"schedule_groups": ["GPV3.1"]}
        )
        self.personal.options = result["data"]
        self.coord.address_data["schedule_groups"] = ["GPV5.1"]
        self.assertEqual(self.sensor.native_value, "GPV3.1")
        self.assertFalse(self.calendar.available)

    async def test_selected_calendar_filters_while_public_keeps_every_group(self):
        self.link()
        events = await self.calendar.async_get_events(self.hass, self.start, self.end)
        self.assertEqual(len(events), 1)
        self.assertIn("group=GPV3.1", events[0].description)
        public = runtime.calendar.DTEKScheduleCalendar(
            self.coord, self.entry, "outage", "outage_schedule", "outage_schedule"
        )
        self.assertEqual(
            len(await public.async_get_events(self.hass, self.start, self.end)), 2
        )
        self.assertEqual(self.sensor.native_value, "GPV3.1")
        self.assertEqual(self.sensor.extra_state_attributes["selection_source"], "user")
        self.assertEqual(
            self.sensor.extra_state_attributes["building_groups"], ["GPV3.1", "GPV5.1"]
        )
        other = mod.AccountScheduleCalendar(self.personal, "100002", "outage")
        other.hass = self.hass
        self.assertFalse(other.available)
        self.assertNotIn("100001", str(self.calendar.extra_state_attributes))

    async def test_group_removed_or_hidden_and_failed_refresh_never_falls_back(self):
        self.link()
        for change, reason in [
            ({"schedule_groups": ["GPV5.1"]}, "group_unavailable"),
            ({"group_labels_visible": False}, "labels_hidden"),
        ]:
            before = dict(self.coord.address_data)
            self.coord.address_data.update(change)
            self.assertEqual(
                self.sensor.extra_state_attributes["selection_status"], reason
            )
            self.assertEqual(self.sensor.native_value, "GPV3.1")
            self.assertFalse(self.calendar.available)
            with self.assertRaises(HomeAssistantError):
                await self.calendar.async_get_events(self.hass, self.start, self.end)
            self.coord.address_data = before
        self.coord.notifications_ready = False
        self.assertFalse(self.calendar.available)
        self.coord.notifications_ready = True
        self.assertTrue(self.calendar.available)
        self.entry.state = ConfigEntryState.NOT_LOADED
        self.assertFalse(self.calendar.available)
        self.hass.config_entries.async_entries.return_value = [self.personal]
        self.assertEqual(
            self.sensor.extra_state_attributes["selection_status"], "no_address"
        )

    async def test_missing_selected_coverage_does_not_mean_no_outages(self):
        self.link()
        self.coord.schedule = replace(
            self.coord.schedule,
            coverage=tuple(
                c for c in self.coord.schedule.coverage if c.group_id == "GPV5.1"
            ),
        )
        with self.assertRaises(HomeAssistantError):
            await self.calendar.async_get_events(self.hass, self.start, self.end)

    async def test_address_reload_reconnects_and_removal_unsubscribes(self):
        self.link()
        self.calendar.async_write_ha_state = Mock()
        await self.calendar.async_added_to_hass()
        self.assertIs(self.calendar._listened, self.coord)
        self.coord.async_update_listeners()
        self.calendar.async_write_ha_state.assert_called()
        self.entry.state = ConfigEntryState.NOT_LOADED
        async_dispatcher_send(
            self.hass,
            mod.SIGNAL_CONFIG_ENTRY_CHANGED,
            ConfigEntryChange.UPDATED,
            self.entry,
        )
        await self.hass.async_block_till_done()
        self.assertIsNone(self.calendar._listened)
        self.entry.state = ConfigEntryState.LOADED
        async_dispatcher_send(
            self.hass,
            mod.SIGNAL_CONFIG_ENTRY_CHANGED,
            ConfigEntryChange.UPDATED,
            self.entry,
        )
        await self.hass.async_block_till_done()
        self.assertIs(self.calendar._listened, self.coord)
        self.calendar._call_on_remove_callbacks()
        self.assertIsNone(self.calendar._listened)

    async def choose_address(self):
        await self.options.async_step_account_schedule({"schedule_account": "100001"})
        return await self.options.async_step_account_schedule_address(
            {"schedule_address": self.entry.entry_id}
        )

    async def test_flow_saves_private_mapping_without_mutating_public_entry(self):
        before_data, before_options = dict(self.entry.data), dict(self.entry.options)
        await self.choose_address()
        result = await self.options.async_step_account_schedule_groups(
            {"schedule_groups": ["GPV3.1"]}
        )
        self.assertEqual(
            result["data"][mod.CONF_ACCOUNT_SCHEDULES]["100001"]["groups"], ["GPV3.1"]
        )
        self.assertEqual(result["data"]["unrelated"], 123)
        self.assertEqual(self.entry.data, before_data)
        self.assertEqual(self.entry.options, before_options)
        self.personal.options = result["data"]
        removed = await self.options.async_step_account_schedule_address(
            {"schedule_address": "none"}
        )
        self.assertEqual(removed["data"][mod.CONF_ACCOUNT_SCHEDULES], {"100001": {"disabled": True}})

    async def test_form_preserves_choices_and_rejects_changed_or_foreign_groups(self):
        await self.choose_address()
        choices = self.options._schedule_group_choices.copy()
        self.coord.address_data["schedule_groups"] = ["GPV5.1"]
        for groups in (["GPV3.1"], ["foreign"], "GPV5.1", [[]]):
            result = await self.options.async_step_account_schedule_groups(
                {"schedule_groups": groups}
            )
            self.assertEqual(result["errors"]["base"], "invalid_schedule_groups")
            self.assertEqual(self.options._schedule_group_choices, choices)
        self.coord.address_success = False
        result = await self.options.async_step_account_schedule_groups(
            {"schedule_groups": ["GPV5.1"]}
        )
        self.assertIn("base", result["errors"])
        empty = await self.options.async_step_account_schedule_groups(
            {"schedule_groups": []}
        )
        self.assertEqual(
            empty["data"][mod.CONF_ACCOUNT_SCHEDULES]["100001"]["groups"], []
        )

    async def test_different_operator_is_not_a_candidate(self):
        self.entry.data["provider_id"] = "dtek_kem"
        self.assertEqual(mod.address_entries(self.hass, self.personal), {})
        await self.options.async_step_account_schedule({"schedule_account": "100001"})
        result = await self.options.async_step_account_schedule_address(
            {"schedule_address": self.entry.entry_id}
        )
        self.assertIn("base", result["errors"])
