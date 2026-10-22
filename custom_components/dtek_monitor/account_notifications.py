"""Opt-in local meter notices, using the existing six-hour profile refresh."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import logging

from homeassistant.components import persistent_notification as pn
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .localization import is_ukrainian
from .account import CONF_ACCOUNTS, account_key
from .providers import CONF_PROVIDER
from .schedule import KYIV_TZ

CONF_NOTICES = "account_meter_notifications"
_LOGGER = logging.getLogger(__name__)


def settings(entry, number):
    return {
        "verification": False,
        "replacement": False,
        "documents": False,
        **entry.options.get(CONF_NOTICES, {}).get(number, {}),
    }


class AccountMeterNotifications:
    def __init__(self, hass, entry, coordinator):
        self.hass, self.entry, self.coordinator = hass, entry, coordinator
        self.store = Store(
            hass, 1, f"dtek_monitor.meter_notifications.{entry.entry_id}"
        )
        self.records = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._unsub = []
        self._tasks = set()
        self._shown = set()
        self._fingerprints = {}
        self._dismissals = set()

    def notification_id(self, key, kind):
        return f"dtek_meter_{self.entry.entry_id}_{key}_{kind}"

    async def start(self):
        try:
            data = await self.store.async_load()
            if data is not None and (
                not isinstance(data, dict)
                or any(
                    not isinstance(row, dict)
                    or not isinstance(row.get("events"), dict)
                    or (
                        row.get("serial") is not None
                        and not isinstance(row["serial"], str)
                    )
                    or any(
                        kind not in ("verification", "replacement")
                        or not isinstance(event, dict)
                        or not isinstance(event.get("fingerprint"), str)
                        or not isinstance(event.get("dismissed"), bool)
                        or not isinstance(event.get("value"), (str, int))
                        for kind, event in row["events"].items()
                    )
                    for row in data.values()
                )
            ):
                raise ValueError
            self.records = data or {}
        except (OSError, ValueError, TypeError):
            _LOGGER.error("Cannot restore DTEK meter notices; notices disabled")
            self._closed = True
            return
        self._unsub = [
            self.coordinator.async_add_listener(self._updated),
            pn.async_register_callback(self.hass, self._notification_changed),
            async_track_time_interval(self.hass, self._tick, timedelta(hours=6)),
        ]
        await self._tick()

    @callback
    def _updated(self):
        if not self._closed:
            self._spawn(self._tick())

    def _spawn(self, coro):
        task = self.hass.async_create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @callback
    def _notification_changed(self, update_type, notifications):
        if self._closed or update_type != pn.UpdateType.REMOVED:
            return
        for key, kind in list(self._shown):
            if self.notification_id(key, kind) in notifications:
                self._shown.discard((key, kind))
                event = self.records[key]["events"][kind]
                fingerprint = event["fingerprint"]
                self._dismissals.add((key, kind, fingerprint))
                self._spawn(self._save_dismissal(key, kind, fingerprint))

    async def _save_dismissal(self, key, kind, fingerprint):
        async with self._lock:
            event = self.records.get(key, {}).get("events", {}).get(kind)
            if not event or event["fingerprint"] != fingerprint:
                return
            event["dismissed"] = True
            try:
                await self.store.async_save(self.records)
            except OSError:
                _LOGGER.error("Cannot save DTEK meter notice dismissal")

    def _dismiss(self, key, kind):
        self._shown.discard((key, kind))
        pn.async_dismiss(self.hass, self.notification_id(key, kind))

    async def _tick(self, now=None):
        async with self._lock:
            if self._closed:
                return
            now = (now or datetime.now(KYIV_TZ)).astimezone(KYIV_TZ)
            updated = deepcopy(self.records)
            selected = {
                account_key(self.entry.data[CONF_PROVIDER], n): n
                for n in self.entry.data[CONF_ACCOUNTS]
            }
            for key in self.records.keys() - selected.keys():
                for kind in ("verification", "replacement"):
                    self._dismiss(key, kind)
                updated.pop(key, None)
            for key, number in selected.items():
                config = settings(self.entry, number)
                row = updated.setdefault(key, {"serial": None, "events": {}})
                snapshot = (self.coordinator.data or {}).get(number)
                profile = (
                    (snapshot.profile or {})
                    if snapshot and self.coordinator.last_update_success
                    else {}
                )
                serial = profile.get("serial")
                if serial:
                    serial = account_key("meter", serial)
                    if (
                        row["serial"]
                        and row["serial"] != serial
                        and config["replacement"]
                    ):
                        row["events"]["replacement"] = {
                            "fingerprint": row["serial"] + serial,
                            "dismissed": False,
                            "value": now.date().isoformat(),
                        }
                    row["serial"] = serial
                year = profile.get("next_verification_year")
                if year:
                    if year <= now.year and config["verification"]:
                        fingerprint = f"{row['serial']}:{year}:{now.year}"
                        if (
                            row["events"].get("verification", {}).get("fingerprint")
                            != fingerprint
                        ):
                            row["events"]["verification"] = {
                                "fingerprint": fingerprint,
                                "dismissed": False,
                                "value": year,
                            }
                    elif year > now.year:
                        row["events"].pop("verification", None)
                for kind in ("verification", "replacement"):
                    if not config[kind]:
                        row["events"].pop(kind, None)
            try:
                if updated != self.records:
                    await self.store.async_save(updated)
            except OSError:
                _LOGGER.error("Cannot save DTEK meter notices; delivery postponed")
                return
            self.records = updated
            if self._closed:
                return
            for key, number in selected.items():
                for kind in ("verification", "replacement"):
                    event = self.records[key]["events"].get(kind)
                    if (
                        not event
                        or event["dismissed"]
                        or (key, kind, event["fingerprint"]) in self._dismissals
                    ):
                        self._dismiss(key, kind)
                        continue
                    marker = (key, kind)
                    # Updating the stable notification ID replaces a changed event.
                    previous = self._fingerprints.get(marker)
                    if marker in self._shown and previous == event["fingerprint"]:
                        continue
                    uk = is_ukrainian(self.hass.config.language)
                    title = (
                        "ДТЕК · лічильник" if uk else "DTEK · meter"
                    ) + f" · ••••{number[-4:]}"
                    value = event["value"]
                    if kind == "verification":
                        message = (
                            f"Кабінет ДТЕК вказує рік наступної повірки: **{value}**. Уточніть точну дату та порядок повірки в оператора. За одним роком неможливо визначити, чи строк уже минув."
                            if uk
                            else f"DTEK lists **{value}** as the next verification year. Check the exact due date and procedure with your operator. A year alone does not establish whether verification is overdue."
                        )
                    else:
                        message = (
                            f"**{value}** у кабінеті змінився номер лічильника. Перевірте, чи була заміна або виправлення даних. Попередню прив’язку джерела енергії не перенесено: перевірте новий лічильник і задайте нові опорні покази."
                            if uk
                            else f"The meter number in the cabinet changed on **{value}**. Check whether the meter was replaced or its details corrected. The existing energy source binding was not transferred: verify the new meter and set new reference readings."
                        )
                    pn.async_create(
                        self.hass, message, title, self.notification_id(key, kind)
                    )
                    self._shown.add(marker)
                    self._fingerprints[marker] = event["fingerprint"]

    async def close(self):
        self._closed = True
        for unsubscribe in self._unsub:
            unsubscribe()
        self._unsub.clear()
        if self._tasks:
            await asyncio.gather(*self._tasks)
