"""Connect public buildings independently of cabinet authentication and polling."""

import asyncio
from contextlib import suppress
from datetime import timedelta

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval

from .account import CONF_ACCOUNTS, CONF_ENTRY_KIND, ENTRY_ACCOUNT
from .building import building_suggestion, normalized, resolve_building
from .const import CONF_CITY, CONF_HOUSE, CONF_STREET, DOMAIN
from .provider import ProviderError
from .providers import CONF_PROVIDER, DEFAULT_PROVIDER_ID, create_provider

LINKS = "account_schedules"


def matching_entries(hass, address):
    return [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.data.get(CONF_ENTRY_KIND) != ENTRY_ACCOUNT
        and e.data.get(CONF_PROVIDER, DEFAULT_PROVIDER_ID) == address.provider_id
        and all(
            normalized(e.data.get(k, ""), k) == normalized(v, k)
            for k, v in zip((CONF_CITY, CONF_STREET, CONF_HOUSE), address.parts)
        )
    ]


async def ensure_building(hass, address, active=lambda: True):
    """Serialize creation across cabinets; import only canonical public fields."""
    lock = hass.data.setdefault("dtek_building_creation_lock", asyncio.Lock())
    async with lock:
        if not active():
            return None
        matches = matching_entries(hass, address)
        if len(matches) > 1:
            return None
        if matches:
            return matches[0]
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "import"},
            data={
                CONF_PROVIDER: address.provider_id,
                CONF_CITY: address.city,
                CONF_STREET: address.street,
                CONF_HOUSE: address.house,
            },
        )
        matches = matching_entries(hass, address)
        if len(matches) == 1:
            return matches[0]
        if result.get("type") == "create_entry":
            return result["result"]
        raise ProviderError("Building setup not completed")


class AccountBuildingLinks:
    """Retry directory outages without blocking setup or overwriting user choices."""

    def __init__(self, hass, entry, coordinator):
        self.hass, self.entry, self.coordinator = hass, entry, coordinator
        self.states = {}
        self._task = None
        self._unsub = None
        self._closed = False

    def start(self):
        self._closed = False
        self._unsub = async_track_time_interval(
            self.hass, self._tick, timedelta(minutes=5)
        )
        self._tick()

    @callback
    def _tick(self, _now=None):
        if not self._closed and (self._task is None or self._task.done()):
            self._task = self.hass.async_create_background_task(
                self.reconcile(), "DTEK building discovery", eager_start=False
            )

    async def close(self):
        self._closed = True
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    def _active(self, number, text):
        return (
            not self._closed
            and self.hass.config_entries.async_get_entry(self.entry.entry_id)
            is self.entry
            and self.entry.data.get(CONF_ACCOUNTS, {}).get(number) == text
            and number not in self.entry.options.get(LINKS, {})
        )

    def _state(self, number, state):
        self.states[number] = state
        self.coordinator.async_update_listeners()

    async def reconcile(self):
        links = {}
        for number, text in list(self.entry.data[CONF_ACCOUNTS].items()):
            if (
                not self._active(number, text)
                or self.states.get(number) == "address_confirmation"
            ):
                continue
            suggestion = building_suggestion(text)
            if not suggestion:
                self._state(number, "address_confirmation")
                continue
            self._state(number, "address_pending")
            provider = None
            try:
                async with asyncio.timeout(30):
                    provider = create_provider(
                        self.entry.data[CONF_PROVIDER], hass=self.hass
                    )
                    address = await resolve_building(provider, suggestion)
                if address is None:
                    self._state(number, "address_confirmation")
                    continue
                building = await ensure_building(
                    self.hass, address, lambda n=number, t=text: self._active(n, t)
                )
                if not self._active(number, text):
                    continue
                if building is None:
                    self._state(number, "address_confirmation")
                    continue
                links[number] = (
                    text,
                    {
                        "address_entry_id": building.entry_id,
                        "groups": [],
                        "selection_source": "automatic",
                    },
                )
            except (ProviderError, TimeoutError):
                self._state(number, "address_lookup_unavailable")
            finally:
                if provider is not None:
                    await provider.close()
        current = dict(self.entry.options.get(LINKS, {}))
        for number, (text, link) in links.items():
            if self._active(number, text):
                current[number] = link
        if current != self.entry.options.get(LINKS, {}) and not self._closed:
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, LINKS: current}
            )
