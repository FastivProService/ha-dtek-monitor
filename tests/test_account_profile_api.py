"""Profile, read-back and comparisons share account permission boundaries."""

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import test_account_history_api as fixtures


@unittest.skipUnless(fixtures.runtime.runtime.HAS_HA, "Requires Home Assistant")
class ProfileAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.HistoryAccessTests.asyncSetUp(self)
        self.api = fixtures.api
        self.account_coord.data = {
            "100001": replace(
                self.snapshot,
                profile={
                    "serial": "PRIVATE-METER",
                    "address": "Example apartment",
                    "eics": ["56X00000000000001"],
                    "installation_eic": "56X00000000000001",
                },
            )
        }
        self.account_coord.last_update_success = True
        self.account_coord.submissions = SimpleNamespace(
            last_attempt=AsyncMock(return_value=None)
        )
        self.account_coord.history.get.return_value = {
            "year": 2026,
            "fetched_at": "2026-10-18T12:00:00+03:00",
            "readings": [],
            "months": [],
        }

    async def invoke(self, command, **values):
        msg = {"id": 1, "entity_id": "sensor.example", **values}
        with (
            patch.object(self.api.er, "async_get", return_value=self.registry),
            patch.object(
                self.api.er,
                "async_entries_for_config_entry",
                return_value=[self.entity],
            ),
        ):
            if command == "profile":
                self.api.websocket_profile(
                    self.hass, self.connection, {"reveal": False, **msg}
                )
            else:
                handler = (
                    self.api.websocket_readback
                    if command == "readback"
                    else self.api.websocket_history
                )
                await handler.__wrapped__(self.hass, self.connection, msg)

    async def test_profile_masked_by_default_local_only_and_independent_of_submission(
        self,
    ):
        await self.invoke("profile")
        result = self.connection.send_result.call_args.args[1]
        self.assertEqual(result["address"], "Example apartment")
        self.assertNotIn("PRIVATE-METER", str(result))
        self.assertNotIn("56X00000000000001", str(result))
        await self.invoke("profile", reveal=True)
        self.assertEqual(
            self.connection.send_result.call_args.args[1]["serial"], "PRIVATE-METER"
        )
        self.account_coord.history.get.assert_not_awaited()
        self.account_coord.submissions.last_attempt.assert_not_awaited()
        self.account_coord.last_update_success = False
        await self.invoke("profile")
        self.assertFalse(self.connection.send_result.call_args.args[1]["available"])

    async def test_denial_blocks_profile_and_history_queries(self):
        self.connection.user.permissions.check_entity.return_value = False
        for command in ("profile", "readback"):
            await self.invoke(command)
        self.connection.send_result.assert_not_called()
        self.account_coord.history.get.assert_not_awaited()
        self.account_coord.submissions.last_attempt.assert_not_awaited()

    async def test_readback_is_readonly_and_permission_rechecked_after_network(self):
        await self.invoke("readback")
        self.assertEqual(
            self.connection.send_result.call_args.args[1]["status"], "no_attempt"
        )
        self.assertTrue(self.account_coord.history.get.call_args.kwargs["force"])
        self.connection.send_result.reset_mock()

        async def revoked(*args, **kwargs):
            self.connection.user.permissions.check_entity.return_value = False
            return {"fetched_at": "2026-10-18T12:00:00+03:00", "readings": []}

        self.account_coord.history.get.side_effect = revoked
        await self.invoke("readback")
        self.connection.send_result.assert_not_called()
        self.assertEqual(self.connection.send_error.call_args.args[1], "unauthorized")

    async def test_readback_uses_attempt_kyiv_year_and_does_not_leak_serial(self):
        self.account_coord.submissions.last_attempt.return_value = {
            "id": "test",
            "status": "unknown",
            "at": "2025-12-31T22:30:00+00:00",
            "meter_serial": "PRIVATE-METER",
            "readings": [{"zone": "GENERAL", "value": 123}],
        }
        await self.invoke("readback")
        self.account_coord.history.get.assert_awaited_once_with(
            "100001", 2026, force=True
        )
        self.assertNotIn("PRIVATE-METER", str(self.connection.send_result.call_args))
        self.assertEqual(
            self.connection.send_result.call_args.args[1]["attempt"]["status"],
            "unknown",
        )

    async def test_comparison_does_not_mutate_cache_and_permission_revocation_stops_result(
        self,
    ):
        current = {
            "year": 2026,
            "fetched_at": "2026-10-18T12:00:00+03:00",
            "readings": [],
            "months": [],
        }
        previous = {**current, "year": 2025}
        self.account_coord.history.get.side_effect = [current, previous]
        await self.invoke("history", year=2026, force=False, compare=True)
        self.assertNotIn("comparison", current)
        self.assertIn("comparison", self.connection.send_result.call_args.args[1])
        self.connection.send_result.reset_mock()

        async def read(number, year, **kwargs):
            if year == 2025:
                self.entry.runtime_data = SimpleNamespace()
            return current if year == 2026 else previous

        self.account_coord.history.get.side_effect = read
        await self.invoke("history", year=2026, force=False, compare=True)
        self.connection.send_result.assert_not_called()
