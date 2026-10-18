"""Real filesystem semantics for the journal that gates outgoing submissions."""

import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from component_loader import load_component_module

store_module = load_component_module("account_submission_store")


class SubmissionStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / ".storage" / "dtek_monitor.submissions.test"
        self.store = store_module.SubmissionStore(self.path, asyncio.to_thread)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_private_atomic_record_is_readable_by_new_store(self):
        self.assertIsNone(await self.store.async_load())
        data = {"hashed-account": {"status": "pending", "id": "attempt"}}
        await self.store.async_save(data)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        restored = store_module.SubmissionStore(self.path, asyncio.to_thread)
        self.assertEqual(await restored.async_load(), data)

    async def test_corrupt_empty_or_wrong_version_file_never_becomes_empty_history(
        self,
    ):
        self.path.parent.mkdir()
        for contents in [
            "",
            "{broken",
            "{}",
            '{"version":2,"data":{}}',
            '{"version":1,"data":null}',
        ]:
            self.path.write_text(contents)
            with self.assertRaises(ValueError):
                await self.store.async_load()
            self.assertEqual(self.path.read_text(), contents)

    async def test_disk_error_is_propagated_and_keeps_previous_record(self):
        data = {"hashed-account": {"status": "pending"}}
        await self.store.async_save(data)
        with patch.object(store_module.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.store.async_save({"hashed-account": {"status": "accepted"}})
        self.assertEqual(await self.store.async_load(), data)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    async def test_cancellation_waits_for_executor_write_before_releasing_caller(self):
        entered, release = threading.Event(), threading.Event()
        real_write = self.store._write

        def slow_write(data):
            entered.set()
            release.wait(5)
            real_write(data)

        self.store._write = slow_write
        task = asyncio.create_task(
            self.store.async_save({"key": {"status": "pending"}})
        )
        self.assertTrue(await asyncio.to_thread(entered.wait, 2))
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(await self.store.async_load(), {"key": {"status": "pending"}})
