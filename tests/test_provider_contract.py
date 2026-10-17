"""Recorded regional shapes and normalized provider invariants, without HA/network."""

from __future__ import annotations
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch
from component_loader import load_component_module

adapter = load_component_module("dtek_provider")
contract = load_component_module("provider")
registry = load_component_module("providers")
client = load_component_module("dtek_client")
snapshot = load_component_module("snapshot")


def fixture(region="oem"):
    return json.loads(
        (Path(__file__).parent / "fixtures/providers" / f"{region}.json").read_text()
    )


def normalize(f):
    now = datetime.fromisoformat(f["captured_at"])
    start = now.astimezone(adapter.KYIV_TZ).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    address = contract.Address(**f["address"])
    reading = adapter.normalize_address(address, f["house_response"], now)
    schedule = adapter.normalize_schedule(
        reading, f["schedule"], start, start + timedelta(days=8), now
    )
    return reading, schedule


class ContractTests(unittest.TestCase):
    def test_four_recorded_operator_shapes_have_current_daily_coverage(self):
        for region in ("oem", "krem", "dnem", "kem"):
            with self.subTest(region=region):
                f = fixture(region)
                address, schedule = normalize(f)
                self.assertEqual(schedule.state, "current")
                self.assertTrue(schedule.intervals)
                self.assertEqual(tuple(g.id for g in schedule.groups), address.groups)
                self.assertEqual(
                    {i.group_id for i in schedule.intervals}, set(address.groups)
                )
                self.assertTrue(
                    all(i.start.tzinfo is not None for i in schedule.intervals)
                )
                self.assertTrue(all(i.source == "daily" for i in schedule.intervals))
                self.assertFalse(schedule.publication.weekly)

    def test_flat_kyiv_and_nested_region_directories(self):
        for region in ("oem", "krem", "dnem", "kem"):
            f = fixture(region)
            spec = registry.PROVIDERS[f["address"]["provider_id"]]
            directory = adapter.normalize_directory(f["streets"], spec.fixed_city)
            self.assertEqual(directory[f["address"]["city"]], (f["address"]["street"],))
        for bad in ([], {}, None, {"city": "street"}, {"city": [1]}):
            with self.assertRaises(contract.ProviderError):
                adapter.normalize_directory(bad, None)

    def test_unknown_and_disabled_operators_cannot_be_enabled_accidentally(self):
        self.assertNotIn("dtek_dem", registry.PROVIDERS)
        for provider_id in ("dtek_dem", "typo"):
            with self.assertRaises(contract.ProviderError):
                registry.create_provider(provider_id)

    def test_multiple_groups_remain_separate_and_order_is_not_identity(self):
        for region in ("krem", "dnem", "kem"):
            f = fixture(region)
            _, original = normalize(f)
            self.assertGreater(len(original.groups), 1)
            entry = next(iter(f["house_response"]["data"].values()))
            entry["sub_type_reason"].reverse()
            _, reordered = normalize(f)
            self.assertEqual(original.revision, reordered.revision)
            self.assertTrue(
                all(
                    i.group_id in {g.id for g in original.groups}
                    for i in original.intervals
                )
            )

    def test_kyiv_internal_groups_have_no_public_queue_label(self):
        _, schedule = normalize(fixture("kem"))
        self.assertTrue(all(g.label is None for g in schedule.groups))
        self.assertTrue(all(g.id.startswith("GPV") for g in schedule.groups))

    def test_hidden_weekly_template_cannot_generate_future_forecasts(self):
        f = fixture()
        _, schedule = normalize(f)
        today = schedule.fetched_at.astimezone(adapter.KYIV_TZ).date()
        self.assertTrue(all(i.start.date() == today for i in schedule.intervals))
        self.assertTrue(all(c.start.date() == today for c in schedule.coverage))

    def test_disabled_publication_keeps_address_but_not_live_schedule(self):
        f = fixture()
        f["house_response"]["showCurSchedule"] = False
        address, schedule = normalize(f)
        self.assertTrue(address.values)
        self.assertEqual(schedule.state, "unpublished")
        self.assertEqual(schedule.intervals, ())
        self.assertEqual(schedule.coverage, ())

    def test_missing_flags_are_unknown_not_publication_permission(self):
        f = fixture()
        f["house_response"].pop("showCurSchedule")
        _, schedule = normalize(f)
        self.assertEqual(schedule.state, "missing")
        self.assertEqual(schedule.intervals, ())

    def test_empty_fact_and_old_preset_are_missing_not_all_clear(self):
        f = fixture()
        f["schedule"]["fact"]["data"] = []
        f["schedule"]["fact"]["update"] = "16.12.2024 00:13"
        _, schedule = normalize(f)
        self.assertEqual(schedule.state, "missing")
        self.assertEqual(schedule.coverage, ())

    def test_confirmed_all_clear_has_coverage_without_outage_intervals(self):
        f = fixture()
        for day in f["schedule"]["fact"]["data"].values():
            for group in day:
                day[group] = {str(h): "yes" for h in range(1, 25)}
        _, schedule = normalize(f)
        self.assertEqual(schedule.state, "current")
        self.assertTrue(schedule.coverage)
        self.assertEqual(schedule.intervals, ())

    def test_incomplete_day_cannot_confirm_all_clear(self):
        f = fixture()
        next(iter(next(iter(f["schedule"]["fact"]["data"].values())).values())).pop(
            "24"
        )
        _, schedule = normalize(f)
        self.assertEqual(schedule.state, "missing")

    def test_new_unknown_slot_code_fails_closed(self):
        f = fixture()
        next(iter(next(iter(f["schedule"]["fact"]["data"].values())).values()))[
            "1"
        ] = "new-code"
        with self.assertRaises(contract.ProviderError):
            normalize(f)

    def test_external_operator_address_is_not_an_ordinary_dtek_group(self):
        f = fixture("dnem")
        next(iter(f["house_response"]["data"].values()))["cek"] = True
        address, schedule = normalize(f)
        self.assertTrue(address.external_operator)
        self.assertEqual(schedule.state, "unsupported")
        self.assertFalse(schedule.intervals)

    def test_dnem_mixed_outage_records_with_cek_cannot_publish_partial_schedule(self):
        f = fixture("dnem")
        house = f["house_response"]["data"]["2"]
        external = deepcopy(house)
        external["cek"] = True
        external["sub_type_reason"] = ["GPV1001.1"]
        f["house_response"]["data"]["2"] = [house, external]
        reading, schedule = normalize(f)
        self.assertTrue(reading.external_operator)
        self.assertIn("GPV1001.1", reading.groups)
        self.assertEqual(schedule.state, "unsupported")
        self.assertEqual(schedule.coverage, ())
        self.assertEqual(schedule.intervals, ())

    def test_absent_house_is_not_confirmed_power_on(self):
        f = fixture()
        f["house_response"]["data"] = {}
        with self.assertRaises(contract.ProviderError):
            normalize(f)

    def test_revision_includes_provider_address_publication_and_labels(self):
        f = fixture()
        address, schedule = normalize(f)
        later = replace(schedule, fetched_at=schedule.fetched_at + timedelta(minutes=1))
        self.assertEqual(schedule.revision, later.revision)
        variants = [
            replace(
                schedule, address=replace(address.address, provider_id="dtek_krem")
            ),
            replace(schedule, address=replace(address.address, house="different")),
            replace(
                schedule, publication=replace(schedule.publication, group_labels=False)
            ),
            replace(
                schedule,
                groups=tuple(replace(g, label="different") for g in schedule.groups),
            ),
        ]
        for variant in variants:
            self.assertNotEqual(schedule.revision, variant.revision)

    def test_ambiguous_address_delimiters_cannot_collide(self):
        a = contract.Address("dtek_oem", "a_b", "c", "1")
        b = contract.Address("dtek_oem", "a", "b_c", "1")
        self.assertNotEqual(a.identity, b.identity)

    def test_existing_snapshot_without_contract_revision_still_loads(self):
        a, s = normalize(fixture())
        start = s.fetched_at.astimezone(adapter.KYIV_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        old = snapshot.ScheduleSnapshot(
            a.address.provider_id,
            a.address.parts,
            a.groups,
            s.fetched_at,
            "17.10.2026 14:52",
            start,
            start + timedelta(days=8),
            tuple(i.as_window() for i in s.intervals),
        )
        self.assertNotIn("contract_revision", old.to_dict())
        loaded = snapshot.ScheduleSnapshot.from_dict(
            old.to_dict(),
            provider_id=a.address.provider_id,
            address=a.address.parts,
            now=s.fetched_at,
        )
        self.assertEqual(old.snapshot_id, loaded.snapshot_id)
        current = replace(old, contract_revision=s.revision)
        self.assertNotEqual(current.snapshot_id, old.snapshot_id)

    def test_daily_all_clear_overrides_even_a_published_weekly_forecast(self):
        f = fixture()
        f["house_response"]["showTablePlan"] = True
        for day in f["schedule"]["fact"]["data"].values():
            for group in day:
                day[group] = {str(h): "yes" for h in range(1, 25)}
        _, schedule = normalize(f)
        today = schedule.fetched_at.astimezone(adapter.KYIV_TZ).date()
        self.assertFalse([i for i in schedule.intervals if i.start.date() == today])
        self.assertTrue([i for i in schedule.intervals if i.start.date() > today])
        self.assertTrue(all(i.kind == "possible" for i in schedule.intervals))

    def test_changed_time_grid_is_rejected_instead_of_misreading_hours(self):
        f = fixture()
        f["schedule"]["preset"]["time_zone"]["1"] = ["00-00:30", "00:00", "00:30"]
        with self.assertRaises(contract.ProviderError):
            normalize(f)


class ProviderAsyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f = fixture()
        self.reading, self.schedule = normalize(self.f)
        self.client = Mock(spec=client.DTEKClient)
        self.client.get_home_status = AsyncMock(return_value=self.f["house_response"])
        self.client.consume_schedule_update.return_value = self.f["schedule"]
        self.client.get_schedule_data.return_value = self.f["schedule"]
        self.client.check_update = AsyncMock(return_value=None)
        self.client.close = AsyncMock()
        self.provider = adapter.DTEKWebProvider(
            registry.PROVIDERS["dtek_oem"], self.client
        )

    async def refresh(self):
        # Fixture time is deterministic even when CI runs on another date.
        real_datetime = adapter.datetime
        with patch.object(adapter, "datetime") as clock:
            clock.now.return_value = self.schedule.fetched_at
            start = self.schedule.fetched_at.astimezone(adapter.KYIV_TZ).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            return await self.provider.refresh(
                self.reading.address, start, start + timedelta(days=8)
            )

    async def test_address_can_succeed_while_schedule_check_fails(self):
        self.client.check_update.side_effect = contract.ProviderError("offline")
        result = await self.refresh()
        self.assertIsNotNone(result.address)
        self.assertIsNone(result.schedule)
        self.assertEqual(result.schedule_error, "offline")

    async def test_schedule_source_verification_cannot_reuse_failed_address(self):
        self.client.get_home_status.side_effect = contract.ProviderError("offline")
        result = await self.refresh()
        self.assertIsNone(result.address)
        self.assertIsNone(result.schedule)
        self.assertIsNotNone(result.schedule_checked_at)

    async def test_mismatched_provider_is_rejected_before_io(self):
        with self.assertRaises(contract.ProviderError):
            await self.provider.refresh(
                replace(self.reading.address, provider_id="dtek_kem"),
                self.schedule.fetched_at,
                self.schedule.fetched_at,
            )
        self.client.get_home_status.assert_not_awaited()

    async def test_close_releases_transport(self):
        await self.provider.close()
        self.client.close.assert_awaited_once()

    async def test_concurrent_clients_keep_their_urls_and_headers(self):
        clients = [
            client.DTEKClient(spec.base_url)
            for spec in (
                registry.PROVIDERS["dtek_oem"],
                registry.PROVIDERS["dtek_krem"],
            )
        ]
        for c in clients:
            c._ensure_session = AsyncMock()
            c._http = Mock()
            c._http.post = AsyncMock(
                return_value=Mock(
                    status_code=200,
                    text='{"result":true}',
                    json=lambda: {"result": True},
                )
            )
        await asyncio.gather(*(c._post({"method": "getStreets"}) for c in clients))
        for c in clients:
            args = c._http.post.call_args
            self.assertEqual(args.args[0], c.base_url + "/ua/ajax")
            self.assertEqual(args.kwargs["headers"]["Origin"], c.base_url)
            self.assertEqual(
                args.kwargs["headers"]["Referer"], c.base_url + "/ua/shutdowns"
            )
        self.assertNotEqual(
            clients[0].xhr_headers["Origin"], clients[1].xhr_headers["Origin"]
        )

    async def test_delta_publication_revocation_wins_over_address_flags(self):
        self.client.check_update.return_value = {
            **self.f["schedule"],
            "showTableSchedule": False,
        }
        result = await self.refresh()
        self.assertEqual(result.schedule.state, "unpublished")
        self.assertFalse(result.schedule.intervals)
        self.assertIsNotNone(result.schedule_error)

    async def test_failed_delta_does_not_commit_candidate_raw_schedule(self):
        first = await self.refresh()
        self.assertEqual(first.schedule.state, "current")
        before = deepcopy(self.provider._raw)
        self.client.consume_schedule_update.return_value = {
            **self.f["schedule"],
            "fact": {"data": {}, "update": "17.10.2026 16:00"},
        }
        self.client.check_update.side_effect = contract.ProviderError("offline")
        result = await self.refresh()
        self.assertEqual(result.schedule_error, "offline")
        self.assertEqual(self.provider._raw, before)
