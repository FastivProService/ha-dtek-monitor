"""Independent, ephemeral account-group refresh and freshness checks."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .account import CONF_ACCOUNTS, AccountAPIChanged, AccountError, AccountRateLimitError
from .account_api_health import clear_api_issue, report_api_issue
from .account_group_client import INTERFACE, create_account_group_provider
from .providers import CONF_PROVIDER

REFRESH_INTERVAL = timedelta(minutes=15)
USE_CABINET_GROUPS = "use_cabinet_groups"


@dataclass(frozen=True)
class GroupResult:
    status: str
    groups: tuple[str, ...] = ()
    checked_at: datetime | None = None


class AccountGroups:
    """Background enrichment cannot delay cabinet setup or meter operations."""

    def __init__(self, hass, entry, coordinator):
        self.hass, self.entry, self.coordinator = hass, entry, coordinator
        self.provider = create_account_group_provider(coordinator.client)
        self.results = {}
        self._closed = False
        self._task = None
        self._unsubscribe = []
        self._notifying = False
        self._observed = None
        self._refresh_again = False
        self._expiry = {}
        self._refresh_lock = asyncio.Lock()

    def _enabled(self, number):
        link = self.entry.options.get("account_schedules", {}).get(number, {})
        return not link.get("disabled") and link.get(USE_CABINET_GROUPS, True)

    def _available(self, number):
        return (
            not self._closed
            and number in self.entry.data.get(CONF_ACCOUNTS, {})
            and self.coordinator.last_update_success
            and (self.coordinator.data or {}).get(number) is not None
            and self._enabled(number)
        )

    def get(self, number):
        if not self._enabled(number):
            return GroupResult("disabled")
        if self.provider is None:
            return GroupResult("unsupported")
        if not self._available(number):
            return GroupResult("unavailable")
        result = self.results.get(number, GroupResult("pending"))
        if result.checked_at and datetime.now(timezone.utc) - result.checked_at >= REFRESH_INTERVAL:
            return GroupResult("expired")
        return result

    def start(self):
        if self.provider is None or self._closed:
            return
        self._unsubscribe = [
            async_track_time_interval(self.hass, self._tick, REFRESH_INTERVAL),
            self.coordinator.async_add_listener(self._updated),
        ]
        self._updated()

    @callback
    def _updated(self):
        if self._notifying or self._closed:
            return
        observed = (id(self.coordinator.data), self.coordinator.last_update_success)
        if observed != self._observed:
            self._observed = observed
            self._refresh_again = True
            self._tick()

    @callback
    def _tick(self, _now=None):
        if not self._closed and (self._task is None or self._task.done()):
            self._refresh_again = False
            self._task = self.hass.async_create_background_task(
                self.refresh(), "DTEK account group metadata", eager_start=False
            )
            self._task.add_done_callback(lambda _task: self._tick() if self._refresh_again else None)

    def _set_result(self, number, result):
        if cancel := self._expiry.pop(number, None):
            cancel()
        if result is None:
            self.results.pop(number, None)
        else:
            self.results[number] = result
            if result.checked_at:
                self._expiry[number] = async_call_later(
                    self.hass, REFRESH_INTERVAL.total_seconds(), self._notify
                )

    @callback
    def _notify(self, _now=None):
        """Notify on HA's event loop, including calls from the expiry timer."""
        if self._closed:
            return
        self._notifying = True
        try:
            self.coordinator.async_update_listeners()
        finally:
            self._notifying = False

    async def refresh(self):
        if self.provider is None or self._closed:
            return
        async with self._refresh_lock:
            if self._closed:
                return
            api_error = None
            all_ok = True
            attempted = False
            rate_limited = False
            selected = tuple(self.entry.data.get(CONF_ACCOUNTS, {}))
            for number in tuple(self.results):
                if number not in selected:
                    self._set_result(number, None)
            if not any(self._enabled(n) for n in selected):
                clear_api_issue(self.hass, self.entry.data[CONF_PROVIDER], INTERFACE, scope=self.entry.entry_id)
            for number in selected:
                if not self._available(number) or rate_limited:
                    self._set_result(number, None)
                    continue
                attempted = True
                # Remember the snapshot itself: a changed account/connection while
                # awaiting a response must not accept a late result.
                snapshot = self.coordinator.data[number]
                try:
                    async with asyncio.timeout(45):
                        groups = await self.provider.read_groups(number)
                    result = GroupResult(
                        "current" if groups else "empty", groups, datetime.now(timezone.utc)
                    )
                except AccountAPIChanged as err:
                    all_ok = False
                    if err.interface == INTERFACE:
                        api_error = err
                        result = GroupResult("api_changed")
                    else:
                        # The modern client's own hook owns main-login repairs.
                        result = GroupResult("unavailable")
                except (AccountError, TimeoutError) as err:
                    all_ok = False
                    rate_limited = isinstance(err, AccountRateLimitError)
                    result = GroupResult("unavailable")
                if self._closed:
                    return
                if self._available(number) and self.coordinator.data.get(number) is snapshot:
                    self._set_result(number, result)
                else:
                    self._set_result(number, None)
                self._notify()
            if self._closed:
                return
            if api_error:
                report_api_issue(self.hass, self.entry.data[CONF_PROVIDER], api_error, scope=self.entry.entry_id)
            elif attempted and all_ok:
                clear_api_issue(self.hass, self.entry.data[CONF_PROVIDER], INTERFACE, scope=self.entry.entry_id)
            self._notify()

    async def close(self):
        self._closed = True
        for unsub in self._unsubscribe:
            unsub()
        self._unsubscribe.clear()
        for cancel in self._expiry.values():
            cancel()
        self._expiry.clear()
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self.results.clear()
        if self.provider:
            await self.provider.close()
