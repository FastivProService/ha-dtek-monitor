"""Optional private profile fields and conservative account-history analysis."""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from component_loader import load_component_module
import test_account as fixtures
import test_account_history as history_fixtures
import test_account_submission as submission_fixtures

profile = load_component_module("account_profile")
analysis = load_component_module("account_analysis")


class ProfileTests(unittest.TestCase):
    def test_eics_remain_distinct_and_optional_bad_fields_stay_unknown(self):
        p = profile.parse_profile(
            {
                "installationEic": "56X0000000000002",
                "serialNum": "TEST-METER",
                "nextVerificationYear": "invalid",
                "creationYear": {},
                "nominalPower": "NaN",
                "meterType": [],
                "installationDate": "bad",
                "seals": None,
            },
            {
                "address": "Example apartment",
                "eic": ["56X0000000000001", "56X0000000000001", None],
                "contractPower": "3,00000",
            },
        )
        self.assertEqual(p["eics"], ["56X0000000000001", "56X0000000000002"])
        self.assertEqual(p["installation_eic"], p["eics"][1])
        self.assertEqual(p["contract_power_kw"], "3")
        for key in (
            "next_verification_year",
            "manufacture_year",
            "model",
            "installation_date",
            "seals",
        ):
            self.assertIsNone(p[key])
        hidden = profile.public_profile(p)
        self.assertNotIn("TEST-METER", str(hidden))
        self.assertNotIn("56X0000000000001", str(hidden))
        self.assertEqual(profile.public_profile(p, reveal=True), p)
        self.assertEqual(p["serial"], "TEST-METER")

    def test_no_extra_private_payload_and_seal_numbers_masked(self):
        p = profile.parse_profile(
            {
                "ownerFullName": "PRIVATE OWNER",
                "unknownSecret": "secret",
                "seals": [
                    {
                        "sealNumber": "SEAL-EXAMPLE",
                        "installationDate": "2020-01-02T00:00:00",
                    }
                ],
                "changeHistory": [{"secret": "secret"}],
            }
        )
        self.assertNotIn("secret", str(p))
        self.assertNotIn("PRIVATE OWNER", str(p))
        self.assertEqual(p["replacement_records"], 1)
        self.assertEqual(p["seals"][0]["installed"], "2020-01-02")
        self.assertNotIn("SEAL-EXAMPLE", str(profile.public_profile(p)))
        fallback = profile.parse_profile(None, {"address": "Example apartment"})
        self.assertFalse(fallback["metadata_available"])
        self.assertEqual(fallback["address"], "Example apartment")
        self.assertIsNone(fallback["replacement_records"])

    def test_same_month_intersection_never_uses_gaps_open_months_or_full_year_totals(
        self,
    ):
        def year(y, items):
            return {
                "year": y,
                "fetched_at": "2026-10-18T10:00:00+03:00",
                "months": history_fixtures.history.parse_months(
                    {
                        "items": [
                            {"date": f"{y}-{m:02d}", "consumption": v} for m, v in items
                        ]
                    },
                    y,
                    history_fixtures.TODAY,
                ),
            }

        result = analysis.compare_consumption(
            year(2026, [(1, -5), (2, 0), (6, 100), (10, 999)]),
            year(2025, [(1, 10), (2, 0), (5, 200), (6, 120), (10, 100)]),
        )
        total = result["periods"][0]
        self.assertEqual(total["months"], [1, 2, 6])
        self.assertEqual(total["current"], "95")
        self.assertEqual(total["previous"], "130")
        self.assertFalse(total["complete"])
        self.assertFalse(result["months"][9]["comparable"])
        self.assertIsNone(result["months"][9]["difference"])
        self.assertEqual(result["periods"][1]["current"], "-5")
        self.assertIsNone(result["periods"][2]["current"])
        self.assertEqual(result["months"][1]["difference"], "0")

    def test_readback_distinguishes_duplicate_partial_missing_and_inactive(self):
        _, _, rows = history_fixtures.history.parse_history_page(
            history_fixtures.page(
                history_fixtures.row(
                    value=123, stamp="2026-10-18T00:00:00", isInactive=True
                )
            ),
            2026,
            history_fixtures.TODAY,
        )
        h = {
            "fetched_at": "2026-10-18T11:00:00+03:00",
            "readings": [r for _, r in rows],
        }
        a = {
            "id": "test",
            "status": "unknown",
            "at": "2026-10-17T22:00:00+00:00",
            "meter_serial": "TEST",
            "readings": [{"zone": "GENERAL", "value": 123}],
        }
        before = deepcopy(a)
        result = analysis.match_readback(a, h, "TEST")
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["matches"][0]["status"], "inactive")
        self.assertEqual(result["attempt"]["status"], "unknown")
        self.assertEqual(a, before)
        self.assertEqual(
            analysis.match_readback(a, {**h, "readings": h["readings"] * 2})["status"],
            "ambiguous",
        )
        self.assertEqual(
            analysis.match_readback(
                {**a, "readings": a["readings"] + [{"zone": "DAY", "value": 10}]}, h
            )["status"],
            "partial",
        )
        self.assertEqual(
            analysis.match_readback(a, h, "OTHER")["status"], "meter_changed"
        )
        self.assertEqual(
            analysis.match_readback({**a, "at": "2026-10-17T01:00:00+03:00"}, h)[
                "status"
            ],
            "not_found",
        )
        self.assertEqual(
            analysis.match_readback({**a, "at": "bad"}, h)["status"], "invalid_attempt"
        )
        self.assertEqual(analysis.match_readback(None, h)["status"], "no_attempt")
        self.assertNotIn("meter_serial", result["attempt"])


class ProfileClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_metadata_failure_and_bad_year_do_not_drop_readings(self):
        client = fixtures.client_module.DTEKAccountClient(
            SimpleNamespace(close=AsyncMock())
        )
        client._accounts = {"100001"}
        client._linked_profiles = {"100001": {"address": "Example apartment"}}
        for metadata in (
            fixtures.account.AccountError("Unavailable"),
            {
                "accountNumber": "100001",
                "nextVerificationYear": "broken",
                "meterType": [],
            },
        ):
            client._get = AsyncMock(
                side_effect=[
                    metadata,
                    fixtures.readings(fixtures.scale()),
                    {"items": []},
                ]
            )
            result = await client.read_account("100001")
            self.assertIsNotNone(result.readings)
            self.assertIsNone(result.verification_year)
            self.assertIsNone(result.model)
            self.assertEqual(result.profile["address"], "Example apartment")
        for metadata in (
            {"accountNumber": "100002"},
            fixtures.account.AccountAuthError("Reauth"),
            fixtures.account.AccountRateLimitError("Limited"),
        ):
            client._get = AsyncMock(side_effect=[metadata])
            with self.assertRaises(fixtures.account.AccountError):
                await client.read_account("100001")
            self.assertEqual(client._get.await_count, 1)

    async def test_read_only_journal_does_not_unlock_unknown_delivery_or_change_reference(
        self,
    ):
        manager = submission_fixtures.mod.SubmissionManager(
            SimpleNamespace(submit_readings=AsyncMock()),
            submission_fixtures.MemoryStore(),
            "dtek_oem",
        )
        await manager._load()
        key = manager._key("100001")
        await manager._save(
            key,
            {
                "status": "pending",
                "signature": "private",
                "source": {"token": "private"},
                "at": "2026-10-18T12:00:00+03:00",
                "readings": [],
            },
        )
        record = await manager.last_attempt("100001")
        self.assertEqual(record["status"], "unknown")
        self.assertNotIn("source", record)
        self.assertNotIn("signature", record)
        self.assertTrue(manager._unresolved("100001"))
        self.assertEqual(manager._journal[key]["status"], "pending")
        manager.client.submit_readings.assert_not_awaited()
