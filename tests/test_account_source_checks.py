"""Read-only candidate checks reuse real source validation without creating drafts."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import test_account_energy as energy_fixture

submission = energy_fixture.submission
from test_account_submission import MemoryStore, context


class SourceCheckTests(unittest.IsolatedAsyncioTestCase):
    new_manager = energy_fixture.EnergyTests.new_manager
    sample = energy_fixture.EnergyTests.sample
    bind = energy_fixture.EnergyTests.bind
    get = energy_fixture.EnergyTests.get

    async def asyncSetUp(self):
        await energy_fixture.EnergyTests.asyncSetUp(self)
        self.journal = MemoryStore()
        self.client = SimpleNamespace(
            read_submission_context=AsyncMock(return_value=context()),
            submit_readings=AsyncMock(),
        )
        self.checker = submission.SubmissionManager(
            self.client, self.journal, "test", self.manager
        )

    async def check(self):
        return await self.checker.check_source("100001", self.allow)

    async def test_calculated_value_without_draft_submission_or_recalibration(self):
        await self.bind()
        before = deepcopy(self.store.data[self.manager.key("100001")])
        self.now += timedelta(hours=1)
        self.sample("105.6")
        result = await self.check()
        self.assertEqual(result["readings"][0]["value"], 1005)
        self.assertEqual(result["readings"][0]["source_at"], self.now.isoformat())
        after = self.store.data[self.manager.key("100001")]
        for key in ("history", "revision"):
            self.assertEqual(before[key], after[key])
        for key in ("anchor_source", "anchor_meter"):
            self.assertEqual(
                before["zones"]["GENERAL"][key], after["zones"]["GENERAL"][key]
            )
        self.assertFalse(self.checker._drafts)
        self.assertIsNone(self.journal.data)
        self.client.submit_readings.assert_not_awaited()

    async def test_unresolved_submission_prevents_check_before_remote_read(self):
        for status in ("pending", "unknown", "partial"):
            self.checker._journal = {self.checker._key("100001"): {"status": status}}
            with self.assertRaisesRegex(submission.SubmissionError, "check_cabinet"):
                await self.check()
        self.client.read_submission_context.assert_not_awaited()
        self.client.submit_readings.assert_not_awaited()

    async def test_no_binding_stale_reset_and_unavailable(self):
        with self.assertRaisesRegex(submission.SubmissionError, "no_energy_source"):
            await self.check()
        await self.bind()
        self.now += timedelta(hours=1)
        with self.assertRaisesRegex(submission.SubmissionError, "source_stale"):
            await self.check()
        self.sample("0")
        with self.assertRaisesRegex(submission.SubmissionError, "source_reset"):
            await self.check()
        self.manager.read_source = Mock(
            side_effect=submission.SubmissionError("source_unavailable")
        )
        with self.assertRaisesRegex(submission.SubmissionError, "source_unavailable"):
            await self.check()
        self.client.submit_readings.assert_not_awaited()

    async def test_meter_replacement_zone_change_lower_value_and_duplicate(self):
        await self.bind()
        for change, code in [
            ({"serial": "new"}, "energy_changed"),
            ({"zones": context(("DAY", "NIGHT"))["zones"]}, "energy_changed"),
            ({"zones": [{"zone": "GENERAL", "previous": "2000"}]}, "invalid_readings"),
        ]:
            self.client.read_submission_context.return_value = {**context(), **change}
            with self.assertRaisesRegex(submission.SubmissionError, code):
                await self.check()
        self.client.read_submission_context.return_value = context()
        self.checker._journal = {
            self.checker._key("100001"): {
                "status": "accepted",
                "signature": submission.digest(
                    {
                        "serial": context()["serial"],
                        "date": context()["date"],
                        "readings": {"GENERAL": 1000},
                    }
                ),
            }
        }
        with self.assertRaisesRegex(submission.SubmissionError, "already_sent"):
            await self.check()
        self.client.submit_readings.assert_not_awaited()

    async def test_two_zones_keep_their_own_values_and_account_binding(self):
        await self.bind(zones=("DAY", "NIGHT"))
        self.now += timedelta(hours=1)
        self.sample("105", "sensor.day")
        self.sample("102", "sensor.night")
        self.client.read_submission_context.return_value = context(("DAY", "NIGHT"))
        result = await self.check()
        self.assertEqual(
            {r["zone"]: r["value"] for r in result["readings"]},
            {"DAY": 1005, "NIGHT": 1002},
        )
        with self.assertRaisesRegex(submission.SubmissionError, "no_energy_source"):
            await self.checker.check_source("100002", self.allow)
        self.client.submit_readings.assert_not_awaited()

    async def test_authorization_rechecked_after_await(self):
        await self.bind()
        self.allow.side_effect = [None, submission.SubmissionError("not_available")]
        with self.assertRaisesRegex(submission.SubmissionError, "not_available"):
            await self.check()
        self.client.submit_readings.assert_not_awaited()
