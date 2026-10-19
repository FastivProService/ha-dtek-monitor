"""Automatic linking, races, independent retries and in-place confirmation."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_account_runtime as runtime
from component_loader import load_component_module

HAS_HA = runtime.runtime.HAS_HA
if HAS_HA:
    mod = load_component_module("account_buildings")
    manual = load_component_module("account_building_flow")
    from homeassistant.config_entries import ConfigEntryState


@unittest.skipUnless(HAS_HA, "Requires Home Assistant")
class BuildingLinkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.AccountRuntimeTests.asyncSetUp(self)
        self.entry.data["accounts"] = {
            "100001": "м. Приклад, вул. Тестова буд. 1, кв.7"
        }
        self.entries = [self.entry]
        self.hass.config_entries.async_entries.side_effect = lambda *a: list(
            self.entries
        )
        self.hass.config_entries.async_get_entry.side_effect = lambda eid: next(
            (e for e in self.entries if e.entry_id == eid), None
        )
        self.hass.config_entries.async_update_entry.side_effect = lambda e, **kw: [
            setattr(e, k, v) for k, v in kw.items()
        ]
        self.provider = SimpleNamespace(
            provider_id="dtek_oem",
            list_localities=AsyncMock(return_value=["Приклад"]),
            list_streets=AsyncMock(return_value=["вулиця Тестова"]),
            list_houses=AsyncMock(return_value=["1", "2"]),
            close=AsyncMock(),
        )
        self.worker = mod.AccountBuildingLinks(
            self.hass, self.entry, self.account_coord
        )
        self.addAsyncCleanup(self.worker.close)
        self.hass.config_entries.flow.async_init.side_effect = self.create

    async def create(self, domain, *, context, data):
        self.assertEqual(context, {"source": "import"})
        public = SimpleNamespace(
            entry_id="building-" + str(len(self.entries)),
            data={"entry_kind": "address", **data},
            state=ConfigEntryState.LOADED,
            title="Test building",
            options={},
            runtime_data=SimpleNamespace(
                address_success=True,
                address_data={
                    "schedule_groups": ["GPV3.1", "GPV5.1"],
                    "group_labels_visible": True,
                },
            ),
        )
        self.entries.append(public)
        return {"type": "create_entry", "result": public}

    async def run_worker(self):
        with patch.object(mod, "create_provider", return_value=self.provider):
            await self.worker.reconcile()

    async def test_new_account_creates_building_and_links_without_private_fields(self):
        await self.run_worker()
        self.assertEqual(len(self.entries), 2)
        data = self.hass.config_entries.flow.async_init.call_args.kwargs["data"]
        self.assertEqual(
            data,
            {
                "provider_id": "dtek_oem",
                "city": "Приклад",
                "street": "вулиця Тестова",
                "house_num": "1",
            },
        )
        link = self.entry.options[mod.LINKS]["100001"]
        self.assertEqual(link["groups"], [])
        self.assertEqual(link["selection_source"], "automatic")
        self.provider.close.assert_awaited_once()
        await self.run_worker()
        self.assertEqual(self.hass.config_entries.flow.async_init.await_count, 1)

    async def test_newly_monitored_account_reuses_same_building(self):
        await self.run_worker()
        self.entry.data["accounts"]["100002"] = "м. Приклад, вул. Тестова буд. 1, кв.9"
        await self.run_worker()
        self.assertEqual(
            self.entry.options[mod.LINKS]["100001"],
            self.entry.options[mod.LINKS]["100002"],
        )
        self.assertEqual(self.hass.config_entries.flow.async_init.await_count, 1)

    async def test_existing_building_is_reused_and_other_operator_is_not(self):
        await self.create(
            "dtek_monitor",
            context={"source": "import"},
            data={
                "provider_id": "dtek_krem",
                "city": "Приклад",
                "street": "вулиця Тестова",
                "house_num": "1",
            },
        )
        await self.run_worker()
        self.assertEqual(len(self.entries), 3)
        self.assertNotEqual(
            self.entry.options[mod.LINKS]["100001"]["address_entry_id"],
            self.entries[1].entry_id,
        )

    async def test_regional_cabinet_discovers_building_in_its_own_directory(self):
        self.entry.data["provider_id"] = "dtek_kem"
        self.provider.provider_id = "dtek_kem"
        with patch.object(
            mod, "create_provider", return_value=self.provider
        ) as factory:
            await self.worker.reconcile()
        factory.assert_called_once_with("dtek_kem", hass=self.hass)
        public = self.entries[-1]
        self.assertEqual(public.data["provider_id"], "dtek_kem")
        self.assertNotIn("username", public.data)
        self.assertNotIn("password", public.data)
        self.assertNotIn("accounts", public.data)
        self.assertEqual(
            self.entry.options[mod.LINKS]["100001"]["address_entry_id"], public.entry_id
        )

    async def test_two_cabinets_serialize_creation_and_keep_independent_links(self):
        second = SimpleNamespace(
            entry_id="account-two",
            data={
                **self.entry.data,
                "accounts": {"100002": "м. Приклад, вул. Тестова буд. 1, кв.9"},
            },
            options={},
        )
        self.entries.append(second)
        other = mod.AccountBuildingLinks(self.hass, second, self.account_coord)
        with patch.object(mod, "create_provider", return_value=self.provider):
            await asyncio.gather(self.worker.reconcile(), other.reconcile())
        self.assertEqual(self.hass.config_entries.flow.async_init.await_count, 1)
        self.assertEqual(
            self.entry.options[mod.LINKS]["100001"]["address_entry_id"],
            second.options[mod.LINKS]["100002"]["address_entry_id"],
        )

    async def test_ambiguous_address_needs_confirmation_without_repeated_lookup(self):
        self.provider.list_houses.return_value = ["1", " 1"]
        await self.run_worker()
        await self.run_worker()
        self.assertEqual(self.worker.states["100001"], "address_confirmation")
        self.hass.config_entries.flow.async_init.assert_not_awaited()
        self.assertEqual(self.provider.list_houses.await_count, 1)

    async def test_directory_failure_retries_and_preserves_account_data(self):
        before = dict(self.entry.data)
        self.provider.list_localities.side_effect = mod.ProviderError("private payload")
        await self.run_worker()
        self.assertEqual(self.worker.states["100001"], "address_lookup_unavailable")
        self.assertEqual(self.entry.data, before)
        self.assertEqual(self.entry.options, {})
        self.provider.list_localities.side_effect = None
        await self.run_worker()
        self.assertIn("100001", self.entry.options[mod.LINKS])
        self.assertEqual(self.provider.close.await_count, 2)

    async def test_user_link_and_explicit_disconnect_are_never_overwritten(self):
        for link in [
            {"address_entry_id": "manual", "groups": ["GPV5.1"]},
            {"disabled": True},
        ]:
            self.entry.options = {mod.LINKS: {"100001": link}, "unrelated": 123}
            await self.run_worker()
            self.assertEqual(
                self.entry.options, {mod.LINKS: {"100001": link}, "unrelated": 123}
            )
        self.provider.list_localities.assert_not_awaited()

    async def test_changed_selection_or_removed_entry_during_lookup_is_respected(self):
        async def mutate(*args):
            self.entry.options = {
                mod.LINKS: {"100001": {"disabled": True}},
                "unrelated": 321,
            }
            return ["1"]

        self.provider.list_houses.side_effect = mutate
        await self.run_worker()
        self.hass.config_entries.flow.async_init.assert_not_awaited()
        self.assertTrue(self.entry.options[mod.LINKS]["100001"]["disabled"])
        self.entry.options = {}

        async def remove(*args):
            self.entries.remove(self.entry)
            return ["1"]

        self.provider.list_houses.side_effect = remove
        await self.run_worker()
        self.hass.config_entries.flow.async_init.assert_not_awaited()
        self.assertEqual(self.entry.options, {})

    async def test_close_cancels_lookup_and_closes_provider(self):
        entered = asyncio.Event()

        async def stall():
            entered.set()
            await asyncio.Event().wait()

        self.provider.list_localities.side_effect = stall
        with patch.object(mod, "create_provider", return_value=self.provider):
            self.worker.start()
            await entered.wait()
            await self.worker.close()
        self.provider.close.assert_awaited_once()
        self.hass.config_entries.flow.async_init.assert_not_awaited()

    async def test_import_checks_directory_and_creates_only_public_fields(self):
        with patch.object(
            runtime.runtime.flow, "create_provider", return_value=self.provider
        ):
            result = await self.flow.async_step_import(
                {
                    "provider_id": "dtek_oem",
                    "city": "Приклад",
                    "street": "вулиця Тестова",
                    "house_num": "1",
                    "password": "do-not-copy",
                    "account": "100001",
                }
            )
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(
            set(result["data"]),
            {
                "entry_kind",
                "provider_id",
                "city",
                "street",
                "house_num",
                "scan_interval",
            },
        )
        self.assertEqual(self.flow.unique_id, "Приклад_вулиця Тестова_1")

    async def test_manual_confirmation_in_same_dialog_then_apartment_group_choice(self):
        options = runtime.runtime.flow.DTEKMonitorOptionsFlow(self.entry)
        options.hass = self.hass
        options._schedule_account = "100001"
        with patch.object(manual, "create_provider", return_value=self.provider):
            step = await options.async_step_account_schedule_address(
                {"schedule_address": "create"}
            )
            self.assertEqual(step["step_id"], "account_building_city")
            step = await options.async_step_account_building_city({"city": "Приклад"})
            self.assertEqual(step["step_id"], "account_building_street")
            step = await options.async_step_account_building_street(
                {"street": "вулиця Тестова"}
            )
            self.assertEqual(step["step_id"], "account_building_house")
            bad = await options.async_step_account_building_house(
                {"house_num": "1, кв.7"}
            )
            self.assertEqual(bad["errors"], {"base": "invalid_building"})
            step = await options.async_step_account_building_house({"house_num": "1"})
            self.assertEqual(step["step_id"], "account_schedule_groups")
            result = await options.async_step_account_schedule_groups(
                {"schedule_groups": ["GPV3.1"]}
            )
        self.assertEqual(result["data"][mod.LINKS]["100001"]["groups"], ["GPV3.1"])
        self.assertEqual(
            self.entries[-1].runtime_data.address_data["schedule_groups"],
            ["GPV3.1", "GPV5.1"],
        )

    async def test_manual_form_preserves_choices_and_rejects_changed_context(self):
        options = runtime.runtime.flow.DTEKMonitorOptionsFlow(self.entry)
        options.hass = self.hass
        options._schedule_account = "100001"
        with patch.object(manual, "create_provider", return_value=self.provider):
            await options.async_step_account_schedule_address(
                {"schedule_address": "create"}
            )
            self.provider.list_localities.return_value = ["New city"]
            step = await options.async_step_account_building_city({"city": "Приклад"})
            self.assertEqual(step["step_id"], "account_building_street")
            self.entry.options = {mod.LINKS: {"100001": {"disabled": True}}}
            step = await options.async_step_account_building_street(
                {"street": "вулиця Тестова"}
            )
            self.assertEqual(step["reason"], "building_context_changed")
        self.hass.config_entries.flow.async_init.assert_not_awaited()
