"""Synthetic photos only: submission/privacy and per-zone attachment contracts."""

import asyncio
import base64
from copy import deepcopy
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from component_loader import load_component_module
from test_account import response
from test_account_submission import context, MemoryStore

photos = load_component_module("account_photos")
submission = load_component_module("account_submission")
client_module = load_component_module("account_client")
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5XcAAAAASUVORK5CYII="


def photo():
    return {"data": PNG, "mime_type": "image/png"}


class PhotoValidationTests(unittest.TestCase):
    def test_all_zones_and_inert_bounded_image_content(self):
        data, meta = photos.validate_photos({"GENERAL": photo()}, ["GENERAL"])
        self.assertEqual(data["GENERAL"], photo())
        self.assertEqual(meta["GENERAL"]["width"], 1)
        self.assertNotIn(PNG, json.dumps(meta))
        for bad in [
            {"data": PNG, "mime_type": "image/jpeg"},
            {"data": "<svg onload=alert(1)>", "mime_type": "image/svg+xml"},
            {"data": "https://example.invalid/image.jpg", "mime_type": "image/jpeg"},
            {"data": base64.b64encode(b"garbage").decode(), "mime_type": "image/png"},
            {"data": "A" * (photos.MAX_PHOTO_ENCODED + 1), "mime_type": "image/png"},
            {**photo(), "filename": "private-name"},
        ]:
            with self.assertRaises(photos.PhotoError):
                photos.validate_photo(bad)
        for bad in [{"DAY_2_ZONE": photo()}, {"OTHER": photo()}, [], "bad"]:
            with self.assertRaises(photos.PhotoError):
                photos.validate_photos(bad, ["DAY_2_ZONE", "NIGHT_2_ZONE"])
        self.assertEqual(photos.validate_photos(None, ["GENERAL"]), ({}, {}))

    def test_oversized_pixel_dimensions_are_rejected_without_decompression(self):
        raw = bytearray(base64.b64decode(PNG))
        raw[16:20] = (100000).to_bytes(4, "big")
        with self.assertRaises(photos.PhotoError):
            photos.validate_photo({"data": base64.b64encode(raw).decode(), "mime_type": "image/png"})


class PhotoSubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = Mock()
        self.client.read_submission_context = AsyncMock(return_value=context())
        self.client.submit_readings = AsyncMock(return_value={"status": "accepted"})
        self.store = MemoryStore()
        self.manager = submission.SubmissionManager(self.client, self.store, "dtek_oem")
        self.allow = Mock()

    async def prepare(self, payload=None):
        return await self.manager.prepare("100001", "owner", context()["revision"],
            {"GENERAL": "123"}, self.allow, photos=payload or {"GENERAL": photo()})

    async def confirm(self, draft, user="owner"):
        return await self.manager.confirm("100001", user, draft["confirmation"], self.allow)

    async def test_prepare_preview_and_journal_never_contain_image_bytes(self):
        original = {"GENERAL": photo()}
        draft = await self.prepare(original)
        self.client.submit_readings.assert_not_awaited()
        self.assertIsNone(self.store.data)
        self.assertIn("GENERAL", draft["photos"])
        self.assertNotIn(PNG, json.dumps(draft))
        original["GENERAL"]["data"] = "changed-after-prepare"
        result = await self.confirm(draft)
        self.assertEqual(self.client.submit_readings.call_args.kwargs["photos"]["GENERAL"]["data"], PNG)
        for view in (result, self.store.data, await self.manager.last_attempt("100001")):
            self.assertNotIn(PNG, json.dumps(view))
            self.assertNotIn("photo_data", json.dumps(view))
        self.assertEqual(result["photos"]["GENERAL"]["mime_type"], "image/png")
        with self.assertRaises(submission.SubmissionError):
            await self.confirm(draft)
        self.client.submit_readings.assert_awaited_once()

    async def test_photo_draft_is_user_bound_and_replacement_uses_new_confirmation(self):
        first = await self.prepare()
        with self.assertRaises(submission.SubmissionError):
            await self.confirm(first, "other")
        latest = await self.manager.prepare("100001", "owner", context()["revision"],
            {"GENERAL": "124"}, self.allow)
        with self.assertRaises(submission.SubmissionError):
            await self.confirm(first)
        await self.confirm(latest)
        self.assertNotIn("photos", self.client.submit_readings.call_args.kwargs)
        # A later confirmation still cannot re-send the already accepted reading.
        await self.manager.close()
        self.assertFalse(self.manager._drafts)

    async def test_partial_zone_photos_and_revoked_permission_never_send(self):
        ctx = context(("DAY_2_ZONE", "NIGHT_2_ZONE"))
        self.client.read_submission_context.return_value = ctx
        with self.assertRaisesRegex(submission.SubmissionError, "photos_incomplete"):
            await self.manager.prepare("100001", "owner", ctx["revision"],
                {"DAY_2_ZONE": "123", "NIGHT_2_ZONE": "124"}, self.allow,
                photos={"DAY_2_ZONE": photo()})
        self.client.read_submission_context.return_value = context()
        draft = await self.prepare()
        self.allow.side_effect = submission.SubmissionError("unauthorized")
        with self.assertRaises(submission.SubmissionError):
            await self.confirm(draft)
        self.client.submit_readings.assert_not_awaited()

    async def test_expiry_unknown_result_and_restart_never_replay_photos(self):
        first = await self.prepare()
        with patch.object(submission, "monotonic", return_value=float("inf")):
            with self.assertRaises(submission.SubmissionError):
                await self.confirm(first)
        draft = await self.prepare()
        self.client.submit_readings.side_effect = submission.AccountError("timeout")
        result = await self.confirm(draft)
        self.assertEqual(result["status"], "unknown")
        restarted = submission.SubmissionManager(self.client, self.store, "dtek_oem")
        self.assertTrue((await restarted.context("100001", self.allow))["blocked"])
        self.assertNotIn(PNG, json.dumps(self.store.data))
        self.client.submit_readings.assert_awaited_once()


class PhotoTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = client_module.DTEKAccountClient(Mock(cookies={}))
        self.client._accounts = {"100001"}
        self.client._ensure_token = AsyncMock()

    async def test_each_zone_photo_is_in_same_single_post_as_readings(self):
        rows = [{"zone": z, "value": v} for z, v in [("DAY_2_ZONE", 123), ("NIGHT_2_ZONE", 456)]]
        payload = [{"index": i, "status": "SUCCESS", "messageCode": "READING_CREATED_WITH_DAY_RANGE"} for i in range(2)]
        self.client._request = AsyncMock(return_value=response(200, payload))
        result = await self.client.submit_readings("100001", rows, Mock(), photos={r["zone"]:photo() for r in rows})
        self.assertEqual(result["status"], "accepted")
        self.client._request.assert_awaited_once()
        sent = self.client._request.call_args.kwargs["json"]["readings"]
        self.assertEqual([r["value"] for r in sent], [123, 456])
        self.assertTrue(all(r["photo"] == PNG and r["photoMimeType"] == "image/png" for r in sent))

    async def test_errors_processing_and_new_codes_never_imply_acceptance_or_retry(self):
        rows = [{"zone": "GENERAL", "value": 123}]
        for status in ("ERROR", "INFO", "WARNING", "QUEUED"):
            self.client._request = AsyncMock(return_value=response(200, [{"index":0,"status":status,"messageCode":"NEW_PROCESSING_CODE"}]))
            result = await self.client.submit_readings("100001", rows, Mock(), photos={"GENERAL":photo()})
            self.assertNotEqual(result["status"], "accepted")
            self.assertEqual(result["upstream_results"][0]["messageCode"], "NEW_PROCESSING_CODE")
            self.client._request.assert_awaited_once()
        for code in ("READING_ARE_LARGE_ADD_PHOTO_TO_CHECK", "READING_IS_SMALLER_THAN_PREVIOUS"):
            self.client._request = AsyncMock(return_value=response(200,[{"index":0,"status":"ERROR","messageCode":code}]))
            result = await self.client.submit_readings("100001",rows,Mock())
            self.assertEqual(result,{"status":"rejected","reason":"photo_required"})
