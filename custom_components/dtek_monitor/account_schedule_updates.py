"""Follow verified apartment group changes and notify without creating Repairs."""

import asyncio
from contextlib import suppress
from copy import deepcopy
import json
import logging

from homeassistant.components import persistent_notification as pn
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.storage import Store

from .localization import is_ukrainian
from .account import CONF_ACCOUNTS, account_key
from .account_schedule import (
    CONF_ACCOUNT_SCHEDULES, SELECTED_STATUSES, automatic_selection, cabinet_groups, selection,
)
from .const import DOMAIN
from .providers import CONF_PROVIDER

_LOGGER = logging.getLogger(__name__)


class AccountScheduleUpdates:
    def __init__(self, hass, entry, coordinator):
        self.hass, self.entry, self.coordinator = hass, entry, coordinator
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.account_group_changes")
        self.records = {}
        self._unsub = []
        self._addresses = {}
        self._task = None
        self._again = False
        self._closed = False
        self._needs_save = False

    async def start(self):
        try:
            saved = await self.store.async_load()
            if saved is not None:
                if not isinstance(saved, dict) or any(
                    not isinstance(row, dict) or not isinstance(row.get("link"), str)
                    or not isinstance(row.get("groups"), list)
                    or any(not isinstance(g, str) for g in row["groups"])
                    for row in saved.values()
                ):
                    raise ValueError("Invalid account group comparison data")
                self.records = saved
        except (OSError, ValueError, TypeError):
            _LOGGER.warning("Cannot restore DTEK apartment group baseline; starting a new comparison")
        self._unsub = [
            self.coordinator.async_add_listener(self._updated),
            async_dispatcher_connect(self.hass, SIGNAL_CONFIG_ENTRY_CHANGED, self._entry_changed),
        ]
        self._updated()

    @callback
    def _entry_changed(self, change, entry):
        ids = {link.get("address_entry_id") for link in self.entry.options.get(CONF_ACCOUNT_SCHEDULES, {}).values()}
        if entry.entry_id in ids or entry.entry_id == self.entry.entry_id:
            self._updated()

    @callback
    def _updated(self):
        if self._closed:
            return
        wanted = {
            coordinator for number in self.entry.data[CONF_ACCOUNTS]
            if (coordinator := selection(self.hass, self.entry, number)[1]) is not None
        }
        for coordinator in set(self._addresses) - wanted:
            self._addresses.pop(coordinator)()
        for coordinator in wanted - self._addresses.keys():
            self._addresses[coordinator] = coordinator.async_add_listener(self._updated)
        self._again = True
        if self._task is None or self._task.done():
            self._task = self.hass.async_create_background_task(
                self._run(), "DTEK apartment group changes", eager_start=False,
            )

    async def _run(self):
        while self._again and not self._closed:
            self._again = False
            await self.reconcile()

    def _resolve(self, number):
        address, coordinator, groups, status = selection(self.hass, self.entry, number)
        replaced = None
        if status == "group_unavailable":
            candidate, resolved = automatic_selection(
                coordinator.address_data["schedule_groups"], cabinet_groups(self.entry, number),
            )
            if resolved in SELECTED_STATUSES:
                replaced = sorted(set(groups))
                groups, status = candidate, resolved
        return address, coordinator, groups, status, replaced

    async def reconcile(self):
        options = self.entry.options
        accounts = dict(self.entry.data[CONF_ACCOUNTS])
        links = deepcopy(options.get(CONF_ACCOUNT_SCHEDULES, {}))
        updated = {}
        notices = []
        observed = {}
        for number in accounts:
            key = account_key(self.entry.data[CONF_PROVIDER], number)
            link = links.get(number, {})
            signature = json.dumps(link, sort_keys=True)
            previous = self.records.get(key)
            if previous and previous["link"] != signature:
                previous = None  # A user edit or a different building is not a DTEK change.
            address, coordinator, groups, status, replaced = self._resolve(number)
            observed[number] = (coordinator, tuple(groups), status, replaced)
            old_groups = previous["groups"] if previous else []
            if replaced:
                old_groups = old_groups or replaced
                link = {**link, "groups": [], "selection_source": "automatic"}
                links[number] = link
                signature = json.dumps(link, sort_keys=True)
            if status not in SELECTED_STATUSES:
                if previous:
                    updated[key] = previous
                continue
            groups = sorted(set(groups))
            updated[key] = {"link": signature, "groups": groups}
            if old_groups and old_groups != groups:
                notices.append((key, number, address.title, old_groups, groups))
        if self._closed:
            return
        try:
            if updated != self.records or self._needs_save:
                self._needs_save = True
                await self.store.async_save(updated)
        except OSError:
            _LOGGER.warning("Cannot save DTEK apartment group baseline; update postponed")
            return
        if self._closed:
            return
        if self.entry.options is not options or self.entry.data[CONF_ACCOUNTS] != accounts:
            self._again = True  # A form saved during disk I/O; never overwrite it.
            return
        for number, before in observed.items():
            _, coordinator, groups, status, replaced = self._resolve(number)
            if (coordinator, tuple(groups), status, replaced) != before:
                self._again = True  # The source changed while persisting; re-evaluate.
                return
        for key in self.records.keys() - updated.keys():
            pn.async_dismiss(self.hass, f"{DOMAIN}_{self.entry.entry_id}_{key}_group_changed")
        self.records = updated
        self._needs_save = False
        if links != options.get(CONF_ACCOUNT_SCHEDULES, {}):
            self.hass.config_entries.async_update_entry(
                self.entry, options={**options, CONF_ACCOUNT_SCHEDULES: links},
            )
        uk = is_ukrainian(self.hass.config.language)
        for key, number, title, old, new in notices:
            transition = f"{', '.join(old)} → {', '.join(new)}"
            message = (
                f"{title}\n\nВаша група відключень змінилася: **{transition}**.\n\nНову групу застосовано автоматично. Усі групи будинку залишаються доступними окремо."
                if uk else f"{title}\n\nYour outage group changed: **{transition}**.\n\nThe new group was applied automatically. All building groups remain available separately."
            )
            pn.async_create(
                self.hass, message,
                ("ДТЕК · моя група" if uk else "DTEK · my group") + f" · ••••{number[-4:]}",
                f"{DOMAIN}_{self.entry.entry_id}_{key}_group_changed",
            )

    async def close(self):
        self._closed = True
        for unsub in [*self._unsub, *self._addresses.values()]:
            unsub()
        self._unsub.clear()
        self._addresses.clear()
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task


async def remove_account_schedule_updates(hass, entry):
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.account_group_changes").async_remove()
    for number in entry.data[CONF_ACCOUNTS]:
        key = account_key(entry.data[CONF_PROVIDER], number)
        pn.async_dismiss(hass, f"{DOMAIN}_{entry.entry_id}_{key}_group_changed")
