"""Explicit read-only cabinet refresh; every account shares the same limit."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import DeviceInfo

from .account import CONF_ACCOUNTS, CONF_ENTRY_KIND, ENTRY_ACCOUNT, account_key
from .const import DOMAIN
from .providers import CONF_PROVIDER


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.data.get(CONF_ENTRY_KIND) == ENTRY_ACCOUNT:
        async_add_entities(
            [
                AccountRefreshButton(entry, number)
                for number in entry.data[CONF_ACCOUNTS]
            ]
        )


class AccountRefreshButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "account_refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:refresh"

    def __init__(self, entry, number):
        self.coordinator = entry.runtime_data
        identity = account_key(entry.data[CONF_PROVIDER], number)
        self._attr_unique_id = f"account:{identity}:refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"account:{identity}")}
        )

    async def async_press(self):
        await self.coordinator.async_manual_refresh()
