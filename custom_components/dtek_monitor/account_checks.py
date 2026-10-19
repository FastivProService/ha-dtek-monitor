"""Per-account read-only scheduled checks. No automatic billing or Telegram calls."""

import asyncio
from copy import deepcopy
from datetime import datetime, time, timedelta
import logging
from time import monotonic

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval

from .account import (
    CONF_ACCOUNTS,
    AccountAuthError,
    AccountError,
    AccountRateLimitError,
    account_key,
)
from .account_reminders import reading_window
from .account_submission import SubmissionError
from .account_submission_store import SubmissionStore
from .providers import CONF_PROVIDER
from .schedule import KYIV_TZ

CONF_CHECKS = "account_checks"
STATUSES = ["idle", "checking", "ready", "blocked", "error", "interrupted"]
_LOGGER = logging.getLogger(__name__)


def settings(entry, number):
    return {
        "enabled": False,
        "time": "19:00:00",
        "frequency": "daily",
        **entry.options.get(CONF_CHECKS, {}).get(number, {}),
    }


def due(config, now, last_day):
    """One check per Kyiv day; catch up today only, including DST clock jumps."""
    return (
        config["enabled"]
        and (last_day is None or last_day < now.date().isoformat())
        and now.time() >= time.fromisoformat(config["time"])
        and (config["frequency"] == "daily" or reading_window(now.date()) is not None)
    )


class AccountChecks:
    def __init__(self, hass, entry, coordinator):
        self.hass, self.entry, self.coordinator = hass, entry, coordinator
        self.store = SubmissionStore(
            hass.config.path(".storage", f"dtek_monitor.checks.{entry.entry_id}"),
            hass.async_add_executor_job,
        )
        self.records = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._failed = False
        self._unsub = None
        self._listeners = set()
        self._last_manual = None

    def key(self, number):
        return account_key(self.entry.data[CONF_PROVIDER], number)

    @callback
    def listen(self, listener):
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def _notify(self):
        if not self._closed:
            for listener in tuple(self._listeners):
                listener()

    async def _save(self, records):
        try:
            await self.store.async_save(records)
        except Exception:
            self._failed = True
            self._notify()
            raise SubmissionError("check_store_unavailable") from None
        self.records = records
        self._notify()

    async def start(self):
        try:
            data = await self.store.async_load()
            if data is not None and (
                not isinstance(data, dict)
                or any(not self._valid_record(v) for v in data.values())
            ):
                raise ValueError
            self.records = data or {}
            recovered = deepcopy(self.records)
            for record in recovered.values():
                for run in record["history"]:
                    if run["status"] == "checking":
                        run.update(status="interrupted", reason="interrupted")
            if recovered != self.records:
                await self._save(recovered)
        except Exception:
            self._failed = True
            _LOGGER.error(
                "DTEK scheduled checks disabled: local journal is unavailable"
            )
            self._notify()
            return
        self._unsub = async_track_time_interval(
            self.hass, self._tick, timedelta(minutes=1)
        )
        await self._tick()
        self._notify()

    @staticmethod
    def _valid_record(record):
        if not isinstance(record, dict) or not isinstance(record.get("history"), list):
            return False
        day = record.get("scheduled_day")
        try:
            if day is not None:
                datetime.strptime(day, "%Y-%m-%d")
        except (TypeError, ValueError):
            return False
        return len(record["history"]) <= 20 and all(
            isinstance(row, dict)
            and row.get("status") in STATUSES
            and isinstance(row.get("at"), str)
            and row.get("trigger") in ("manual", "scheduled")
            for row in record["history"]
        )

    async def close(self):
        self._closed = True
        if self._unsub:
            self._unsub()
            self._unsub = None
        # Finish any in-flight read before the provider and source manager close.
        async with self._lock:
            self._listeners.clear()

    def _authorize(self, number):
        if self._closed or number not in self.entry.data[CONF_ACCOUNTS]:
            raise SubmissionError("not_available")
        if self._failed:
            raise SubmissionError("check_store_unavailable")

    def view(self, number):
        config = settings(self.entry, number)
        record = deepcopy(self.records.get(self.key(number), {"history": []}))
        history = record["history"]
        last = history[-1] if history else {}
        return {
            "status": "error" if self._failed else last.get("status", "idle"),
            "reason": "check_store_unavailable" if self._failed else last.get("reason"),
            "checked_at": last.get("at"),
            "readings": last.get("readings", []),
            "mode": "check_only",
            "submitted": False,
            "schedule_enabled": config["enabled"],
            "frequency": config["frequency"],
            "time": config["time"],
            "timezone": "Europe/Kyiv",
            "history": history,
        }

    async def _tick(self, now=None):
        async with self._lock:
            if self._closed or self._failed:
                return
            now = (now or datetime.now(KYIV_TZ)).astimezone(KYIV_TZ)
            for number in list(self.entry.data[CONF_ACCOUNTS]):
                if self._closed:
                    return
                config = settings(self.entry, number)
                if due(
                    config,
                    now,
                    self.records.get(self.key(number), {}).get("scheduled_day"),
                ):
                    try:
                        await self._run(number, now, "scheduled")
                    except SubmissionError:
                        if self._failed:
                            _LOGGER.error(
                                "DTEK scheduled checks stopped: local journal is unavailable"
                            )
                        return

    async def check_now(self, number):
        async with self._lock:
            self._authorize(number)
            now = monotonic()
            if self._last_manual is not None and now - self._last_manual < 60:
                raise SubmissionError("check_cooldown")
            self._last_manual = now
            await self._run(number, datetime.now(KYIV_TZ), "manual")
            return self.view(number)

    async def _run(self, number, now, trigger):
        self._authorize(number)
        records = deepcopy(self.records)
        record = records.setdefault(self.key(number), {"history": []})
        run = {
            "at": now.isoformat(),
            "trigger": trigger,
            "status": "checking",
            "submitted": False,
        }
        record["history"] = [*record["history"], run][-20:]
        if trigger == "scheduled":
            record["scheduled_day"] = now.date().isoformat()
        # Persist the reservation before awaiting the cabinet. Restart never replays it.
        await self._save(records)
        self._authorize(number)
        try:
            result = await self.coordinator.submissions.check_source(
                number, lambda: self._authorize(number)
            )
            self._authorize(number)
            run.update(status="ready", **result)
        except AccountAuthError:
            run.update(status="error", reason="auth_required")
            if not self._closed:
                self.entry.async_start_reauth(self.hass)
        except AccountRateLimitError:
            run.update(status="error", reason="rate_limited")
        except SubmissionError as error:
            run.update(status="blocked", reason=error.code)
        except AccountError:
            run.update(status="error", reason="service_unavailable")
        except Exception:
            # Never persist/log upstream text, credentials or account identifiers.
            run.update(status="error", reason="check_failed")
        if self._closed:
            run.update(status="interrupted", reason="interrupted")
        await self._save(records)
