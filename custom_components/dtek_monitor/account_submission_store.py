"""Durable attempt journal: disk errors must propagate before any billing POST.

HA's general Store intentionally recovers corrupt JSON as empty and can log
write errors without raising. Neither behavior is appropriate for this barrier.
"""

import asyncio
import json
import os
from pathlib import Path
import tempfile


class SubmissionStore:
    def __init__(self, path, executor):
        self.path = Path(path)
        self.executor = executor

    async def _run(self, function, *args):
        work = asyncio.ensure_future(self.executor(function, *args))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            # Do not release the manager lock while an old write can still
            # overwrite the next operation's journal in the executor thread.
            try:
                await asyncio.shield(work)
            finally:
                raise

    async def async_load(self):
        return await self._run(self._read)

    def _read(self):
        try:
            with self.path.open(encoding="utf-8") as stream:
                envelope = json.load(stream)
        except FileNotFoundError:
            return None
        if (
            not isinstance(envelope, dict)
            or envelope.get("version") != 1
            or not isinstance(envelope.get("data"), dict)
        ):
            raise ValueError("Invalid submission journal")
        return envelope["data"]

    async def async_save(self, data):
        await self._run(self._write, data)

    def _write(self, data):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=self.path.name + ".",
                delete=False,
            ) as stream:
                temp = Path(stream.name)
                os.fchmod(stream.fileno(), 0o600)
                json.dump(
                    {"version": 1, "data": data},
                    stream,
                    ensure_ascii=False,
                    allow_nan=False,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
            # Persist the replacement directory entry as well as the file data.
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
