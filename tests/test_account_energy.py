"""Synthetic meter references: no physical devices or billing writes."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, Mock, patch

from component_loader import load_component_module
from test_account_submission import MemoryStore, context

mod = load_component_module("account_energy")
submission = load_component_module("account_submission")


class EnergyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        self.store = MemoryStore()
        self.samples = {}
        self.sample("100")
        self.allow = Mock()
        self.manager = self.new_manager()

    def new_manager(self):
        return mod.EnergyManager(
            self.store,
            "test",
            lambda entity: deepcopy(self.samples[entity]),
            lambda: self.now,
        )

    def sample(self, value, entity="sensor.grid", unit="kWh", fingerprint="device-one"):
        self.samples[entity] = {
            "value": value,
            "at": self.now.isoformat(),
            "unit": unit,
            "fingerprint": fingerprint,
        }
        return self.samples[entity]

    async def bind(
        self, mode="offset", reference="1000", zones=("GENERAL",), freshness="reported"
    ):
        entities = {
            zone: "sensor.grid" if zone == "GENERAL" else "sensor." + zone.lower()
            for zone in zones
        }
        for entity in entities.values():
            if entity not in self.samples:
                self.sample("100", entity)
        draft = await self.manager.preview(
            "100001",
            "user",
            context(zones),
            {
                "mode": mode,
                "entities": entities,
                "max_age": 900,
                "max_power_kw": "30",
                "freshness": freshness,
            },
            {zone: reference for zone in zones},
            None,
            self.allow,
        )
        self.assertIsNone(self.store.data)
        await self.manager.save("100001", "user", draft["confirmation"], self.allow)
        return await self.get()

    async def get(self, number="100001"):
        return await self.manager.get(number, "user", self.allow)

    async def calibrate(self, value):
        view = await self.get()
        return await self.manager.preview(
            "100001",
            "user",
            None,
            None,
            {"GENERAL": value},
            view["binding"]["revision"],
            self.allow,
        )

    async def test_positive_correction_rebases_once_and_survives_restart(self):
        await self.bind()
        self.now += timedelta(days=30)
        self.sample("125")
        self.assertEqual((await self.get())["readings"]["GENERAL"]["value"], "1025")
        draft = await self.calibrate("1030")
        self.assertEqual(draft["change"]["changes"]["GENERAL"]["correction"], "5")
        await self.manager.save("100001", "user", draft["confirmation"], self.allow)
        self.manager = self.new_manager()
        self.now += timedelta(hours=1)
        self.sample("135")
        result = await self.get()
        self.assertEqual(result["readings"]["GENERAL"]["value"], "1040")
        self.assertEqual(len(result["binding"]["history"]), 2)
        with self.assertRaisesRegex(mod.SubmissionError, "energy_confirmation_expired"):
            await self.manager.save("100001", "user", draft["confirmation"], self.allow)
        self.assertEqual((await self.get())["readings"]["GENERAL"]["value"], "1040")

    async def test_negative_correction_and_fractional_carry(self):
        await self.bind(reference="1000.25")
        self.now += timedelta(hours=1)
        self.sample("105.6")
        view = await self.get()
        self.assertEqual(view["readings"]["GENERAL"]["value"], "1005.85")
        self.assertEqual(view["readings"]["GENERAL"]["send_value"], "1005")
        draft = await self.calibrate("1003.5")
        self.assertEqual(draft["change"]["changes"]["GENERAL"]["correction"], "-2.35")
        await self.manager.save("100001", "user", draft["confirmation"], self.allow)
        self.now += timedelta(hours=1)
        self.sample("106.2")
        self.assertEqual(
            Decimal((await self.get())["readings"]["GENERAL"]["value"]),
            Decimal("1004.1"),
        )

    async def test_confirmation_keeps_captured_anchor_not_later_source_value(self):
        await self.bind()
        draft = await self.calibrate("1005")
        self.now += timedelta(minutes=2)
        self.sample("100.5")
        await self.manager.save("100001", "user", draft["confirmation"], self.allow)
        view = await self.get()
        self.assertEqual(view["binding"]["zones"]["GENERAL"]["anchor_source"], "100")
        self.assertEqual(view["readings"]["GENERAL"]["value"], "1005.5")

    async def test_direct_mode_uses_meter_value_without_offsets(self):
        await self.bind(mode="direct")
        self.now += timedelta(hours=1)
        self.sample("110.75")
        view = await self.get()
        self.assertEqual(view["readings"]["GENERAL"]["value"], "110.75")
        with self.assertRaisesRegex(mod.SubmissionError, "invalid_energy"):
            await self.calibrate("999")

    async def test_tariff_zones_are_independent_and_all_required(self):
        await self.bind(zones=("DAY", "NIGHT"))
        self.now += timedelta(hours=1)
        self.sample("103.5", "sensor.day")
        self.sample("101.25", "sensor.night")
        view = await self.get()
        self.assertEqual(view["readings"]["DAY"]["value"], "1003.5")
        self.assertEqual(view["readings"]["NIGHT"]["value"], "1001.25")
        with self.assertRaisesRegex(mod.SubmissionError, "invalid_energy"):
            await self.manager.preview(
                "100001",
                "user",
                None,
                None,
                {"DAY": "1004"},
                view["binding"]["revision"],
                self.allow,
            )

    async def test_stale_unknown_and_gap_recovery_never_invent_zero(self):
        await self.bind()
        self.now += timedelta(days=1)
        self.assertEqual(
            (await self.get())["readings"]["GENERAL"]["error"], "source_stale"
        )
        saved = self.manager.read_source
        self.manager.read_source = Mock(
            side_effect=mod.SubmissionError("source_unavailable")
        )
        self.assertFalse((await self.get())["ready"])
        self.manager.read_source = saved
        self.sample("110")
        self.assertEqual((await self.get())["readings"]["GENERAL"]["value"], "1010")
        self.sample("110")
        self.assertTrue((await self.get())["ready"])

    async def test_zero_import_with_repeated_reports_remains_ready(self):
        await self.bind()
        for _ in range(10):
            self.now += timedelta(days=1)
            self.sample("100")
            view = await self.get()
            self.assertTrue(view["ready"])
            self.assertEqual(view["readings"]["GENERAL"]["value"], "1000")
        self.now += timedelta(minutes=16)
        self.assertFalse((await self.get())["ready"])

    async def test_availability_mode_keeps_report_time_and_survives_restart(self):
        await self.bind(freshness="availability")
        reported = self.samples["sensor.grid"]["at"]
        self.now += timedelta(days=30)
        self.manager = self.new_manager()
        view = await self.get()
        self.assertTrue(view["ready"])
        self.assertEqual(view["readings"]["GENERAL"]["source"]["at"], reported)
        source = self.manager.read_source
        self.manager.read_source = Mock(
            side_effect=mod.SubmissionError("source_unavailable")
        )
        self.assertEqual(
            (await self.get())["readings"]["GENERAL"]["error"], "source_unavailable"
        )
        with self.assertRaisesRegex(mod.SubmissionError, "source_unavailable"):
            self.manager.guard_proposal("100001", "user", view["proposal"], self.allow)
        self.manager.read_source = source
        self.sample("100.25")
        self.assertEqual((await self.get())["readings"]["GENERAL"]["value"], "1000.25")
        self.sample("0")
        self.assertEqual(
            (await self.get())["readings"]["GENERAL"]["error"], "source_reset"
        )

    async def test_missing_policy_keeps_strict_freshness_and_invalid_policy_fails(self):
        await self.bind()
        del self.store.data[self.manager.key("100001")]["freshness"]
        self.manager = self.new_manager()
        self.now += timedelta(days=1)
        self.assertFalse((await self.get())["ready"])
        self.store.data[self.manager.key("100001")]["freshness"] = "ignore_everything"
        self.manager = self.new_manager()
        with self.assertRaisesRegex(mod.SubmissionError, "energy_store_unavailable"):
            await self.get()

    async def test_observed_reset_is_latched_even_after_rebound_and_restart(self):
        await self.bind()
        self.now += timedelta(hours=1)
        await self.manager.observe("sensor.grid", self.sample("0"))
        self.sample("110")
        self.manager = self.new_manager()
        view = await self.get()
        self.assertFalse(view["ready"])
        self.assertEqual(view["readings"]["GENERAL"]["error"], "source_reset")
        draft = await self.calibrate("1010")
        await self.manager.save("100001", "user", draft["confirmation"], self.allow)
        self.assertTrue((await self.get())["ready"])

    async def test_new_binding_draft_also_observes_reset_before_first_save(self):
        draft = await self.manager.preview(
            "100001",
            "user",
            context(),
            {
                "mode": "offset",
                "entities": {"GENERAL": "sensor.grid"},
                "max_age": 900,
                "max_power_kw": "30",
            },
            {"GENERAL": "1000"},
            None,
            self.allow,
        )
        self.assertTrue(self.manager.uses("sensor.grid"))
        await self.manager.observe("sensor.grid", self.sample("0"))
        self.sample("101")
        with self.assertRaisesRegex(mod.SubmissionError, "source_reset"):
            await self.manager.save("100001", "user", draft["confirmation"], self.allow)
        self.assertIsNone(self.store.data)

    async def test_device_unit_and_implausible_jump_require_verification(self):
        for changes, reason in [
            ({"fingerprint": "new-device"}, "source_changed"),
            ({"unit": "Wh"}, "source_changed"),
            ({"value": "1000"}, "source_jump"),
        ]:
            self.store = MemoryStore()
            self.sample("100")
            self.manager = self.new_manager()
            await self.bind()
            self.now += timedelta(seconds=1)
            self.sample("100", **{k: v for k, v in changes.items() if k != "value"})
            self.samples["sensor.grid"].update(changes)
            view = await self.get()
            self.assertEqual(view["readings"]["GENERAL"]["error"], reason)

    async def test_tokens_bind_user_account_revision_and_expiry(self):
        view = await self.bind()
        draft = await self.calibrate("1005")
        for number, user in [("100002", "user"), ("100001", "other")]:
            with self.assertRaises(mod.SubmissionError):
                await self.manager.save(number, user, draft["confirmation"], self.allow)
        await self.manager.save("100001", "user", draft["confirmation"], self.allow)
        with self.assertRaisesRegex(mod.SubmissionError, "energy_changed"):
            await self.manager.validate_proposal(
                "100001",
                "user",
                view["proposal"],
                context(),
                {"GENERAL": "1000"},
                self.allow,
            )
        d = await self.calibrate("1006")
        with patch.object(mod, "monotonic", return_value=float("inf")):
            with self.assertRaisesRegex(
                mod.SubmissionError, "energy_confirmation_expired"
            ):
                await self.manager.save("100001", "user", d["confirmation"], self.allow)

    async def test_permission_revocation_and_write_failure_prevent_reference_change(
        self,
    ):
        await self.bind()
        draft = await self.calibrate("1005")
        self.allow.side_effect = mod.SubmissionError("unauthorized")
        with self.assertRaisesRegex(mod.SubmissionError, "unauthorized"):
            await self.manager.save("100001", "user", draft["confirmation"], self.allow)
        self.allow.side_effect = None
        self.store.async_save = AsyncMock(side_effect=OSError("disk full"))
        with self.assertRaisesRegex(mod.SubmissionError, "energy_store_unavailable"):
            await self.manager.save("100001", "user", draft["confirmation"], self.allow)
        with self.assertRaisesRegex(mod.SubmissionError, "energy_store_unavailable"):
            await self.get()
        self.assertEqual(
            next(iter(self.store.data.values()))["zones"]["GENERAL"]["anchor_meter"],
            "1000",
        )

    async def test_corrupt_store_is_not_treated_as_empty(self):
        for bad in ([], {"key": {}}, {"key": {"mode": "offset"}}):
            self.store.data = bad
            self.manager = self.new_manager()
            with self.assertRaisesRegex(
                mod.SubmissionError, "energy_store_unavailable"
            ):
                await self.get()

    async def test_other_apartment_not_linked_and_removal_invalidates_proposals(self):
        view = await self.bind()
        self.assertIsNone((await self.get("100002"))["binding"])
        await self.manager.remove("100001", view["binding"]["revision"], self.allow)
        with self.assertRaisesRegex(mod.SubmissionError, "energy_changed"):
            await self.manager.validate_proposal(
                "100001",
                "user",
                view["proposal"],
                context(),
                {"GENERAL": "1000"},
                self.allow,
            )

    async def test_accepted_rejected_unknown_submissions_do_not_rebase(self):
        await self.bind(reference="1000.5")
        for status in ("accepted", "rejected", "unknown"):
            client = Mock(
                read_submission_context=AsyncMock(return_value=context()),
                submit_readings=AsyncMock(return_value={"status": status}),
            )
            sender = submission.SubmissionManager(
                client, MemoryStore(), "test", self.manager
            )
            view = await self.get()
            draft = await sender.prepare(
                "100001",
                "user",
                context()["revision"],
                {"GENERAL": "1000"},
                self.allow,
                source_token=view["proposal"],
            )
            result = await sender.confirm(
                "100001", "user", draft["confirmation"], self.allow
            )
            self.assertEqual(result["status"], status)
            binding = (await self.get())["binding"]
            self.assertEqual(binding["zones"]["GENERAL"]["anchor_meter"], "1000.5")
            self.assertEqual(len(binding["history"]), 1)

    async def test_calibration_persists_even_if_dtek_rejects_and_next_calculation_is_correct(
        self,
    ):
        await self.bind()
        self.now += timedelta(days=30)
        self.sample("125")
        correction = await self.calibrate("1030")
        await self.manager.save(
            "100001", "user", correction["confirmation"], self.allow
        )
        client = Mock(
            read_submission_context=AsyncMock(return_value=context()),
            submit_readings=AsyncMock(return_value={"status": "rejected"}),
        )
        sender = submission.SubmissionManager(
            client, MemoryStore(), "test", self.manager
        )
        draft = await sender.prepare(
            "100001", "user", context()["revision"], {"GENERAL": "1030"}, self.allow
        )
        await sender.confirm("100001", "user", draft["confirmation"], self.allow)
        self.manager = self.new_manager()
        self.now += timedelta(hours=1)
        self.sample("135")
        self.assertEqual((await self.get())["readings"]["GENERAL"]["value"], "1040")

    async def test_source_reset_after_review_blocks_post_but_manual_entry_still_works(
        self,
    ):
        view = await self.bind()
        client = Mock(
            read_submission_context=AsyncMock(return_value=context()),
            submit_readings=AsyncMock(return_value={"status": "accepted"}),
        )
        sender = submission.SubmissionManager(
            client, MemoryStore(), "test", self.manager
        )
        draft = await sender.prepare(
            "100001",
            "user",
            context()["revision"],
            {"GENERAL": "1000"},
            self.allow,
            source_token=view["proposal"],
        )
        self.sample("0")
        with self.assertRaisesRegex(mod.SubmissionError, "energy_changed"):
            await sender.confirm("100001", "user", draft["confirmation"], self.allow)
        client.submit_readings.assert_not_awaited()
        draft = await sender.prepare(
            "100001", "user", context()["revision"], {"GENERAL": "1000"}, self.allow
        )
        await sender.confirm("100001", "user", draft["confirmation"], self.allow)
        client.submit_readings.assert_awaited_once()

    async def test_source_proposal_cannot_cross_meter_or_change_values(self):
        view = await self.bind()
        for ctx, values in [
            ({**context(), "serial": "another-flat-meter"}, {"GENERAL": "1000"}),
            (context(), {"GENERAL": "1005"}),
            (context(("DAY", "NIGHT")), {"GENERAL": "1000"}),
        ]:
            with self.assertRaisesRegex(mod.SubmissionError, "energy_changed"):
                await self.manager.validate_proposal(
                    "100001", "user", view["proposal"], ctx, values, self.allow
                )

    async def test_change_during_journal_write_is_caught_before_transport(self):
        view = await self.bind()
        client = Mock(
            read_submission_context=AsyncMock(return_value=context()),
            submit_readings=AsyncMock(return_value={"status": "accepted"}),
        )
        store = MemoryStore()
        original = store.async_save

        async def save(data):
            await original(data)
            self.sample("0")

        store.async_save = save
        sender = submission.SubmissionManager(client, store, "test", self.manager)
        draft = await sender.prepare(
            "100001",
            "user",
            context()["revision"],
            {"GENERAL": "1000"},
            self.allow,
            source_token=view["proposal"],
        )
        with self.assertRaisesRegex(mod.SubmissionError, "source_reset"):
            await sender.confirm("100001", "user", draft["confirmation"], self.allow)
        client.submit_readings.assert_not_awaited()
        self.assertEqual(next(iter(store.data.values()))["status"], "not_sent")

    async def test_two_calibration_drafts_cannot_overwrite_each_other(self):
        await self.bind()
        first = await self.calibrate("1001")
        second = await self.calibrate("1002")
        await self.manager.save("100001", "user", first["confirmation"], self.allow)
        with self.assertRaisesRegex(mod.SubmissionError, "energy_changed"):
            await self.manager.save(
                "100001", "user", second["confirmation"], self.allow
            )
        self.assertEqual(
            (await self.get())["binding"]["zones"]["GENERAL"]["anchor_meter"], "1001"
        )

    async def test_invalid_reference_and_shared_tariff_source_are_rejected(self):
        await self.bind()
        for value in ("NaN", "Infinity", "-1", "1e3", "1000000", "1,5", ""):
            with self.assertRaisesRegex(mod.SubmissionError, "invalid_energy"):
                await self.calibrate(value)
        view = await self.get()
        with self.assertRaisesRegex(mod.SubmissionError, "invalid_energy"):
            await self.manager.preview(
                "100001",
                "user",
                context(("DAY", "NIGHT")),
                {
                    "mode": "offset",
                    "entities": {"DAY": "sensor.grid", "NIGHT": "sensor.grid"},
                    "max_age": 900,
                    "max_power_kw": "30",
                },
                {"DAY": "1000", "NIGHT": "1000"},
                view["binding"]["revision"],
                self.allow,
            )

    async def test_persisted_reference_round_trip_uses_durable_store(self):
        import asyncio
        import tempfile
        from pathlib import Path

        store_module = load_component_module("account_submission_store")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "references"
            self.store = store_module.SubmissionStore(path, asyncio.to_thread)
            self.manager = self.new_manager()
            draft = await self.manager.preview(
                "100001",
                "user",
                context(),
                {
                    "mode": "offset",
                    "entities": {"GENERAL": "sensor.grid"},
                    "max_age": 900,
                    "max_power_kw": "30",
                },
                {"GENERAL": "1000"},
                None,
                self.allow,
            )
            await self.manager.save("100001", "user", draft["confirmation"], self.allow)
            self.manager = self.new_manager()
            self.now += timedelta(days=1)
            self.sample("125")
            correction = await self.calibrate("1030")
            await self.manager.save(
                "100001", "user", correction["confirmation"], self.allow
            )
            self.manager = self.new_manager()
            self.now += timedelta(hours=1)
            self.sample("135")
            self.assertEqual((await self.get())["readings"]["GENERAL"]["value"], "1040")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
