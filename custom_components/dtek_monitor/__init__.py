"""DTEK Monitor integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import DTEKDataCoordinator
from .providers import CONF_PROVIDER, DEFAULT_PROVIDER_ID, create_provider
from .migration import migrate_device_identifiers, migrate_entity_unique_id
from .account import CONF_ENTRY_KIND, ENTRY_ACCOUNT
from .account_client import create_account_provider

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.SENSOR,
]
ACCOUNT_PLATFORMS = [Platform.SENSOR, Platform.BUTTON]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    from .account_history_api import async_setup_history

    await async_setup_history(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DTEK Monitor from a config entry."""
    if entry.data.get(CONF_ENTRY_KIND) == ENTRY_ACCOUNT:
        from .account_coordinator import AccountCoordinator
        from .account_sensor import remove_unselected_accounts

        remove_unselected_accounts(hass, entry)
        client = create_account_provider(entry.data[CONF_PROVIDER])
        coordinator = AccountCoordinator(hass, entry, client)
        try:
            await coordinator.async_config_entry_first_refresh()
            entry.runtime_data = coordinator
            entry.async_on_unload(entry.add_update_listener(async_reload_entry))
            await hass.config_entries.async_forward_entry_setups(
                entry, ACCOUNT_PLATFORMS
            )
        except BaseException:
            await client.close()
            raise
        return True
    await _async_migrate_registry_identifiers(hass, entry)

    provider = create_provider(entry.data.get(CONF_PROVIDER, DEFAULT_PROVIDER_ID))

    coordinator = DTEKDataCoordinator(hass, provider, entry)
    await coordinator.async_load_snapshot()
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        if coordinator.snapshot is None and not coordinator.address_success:
            await provider.close()
            raise
        _LOGGER.info(
            "DTEK schedule unavailable; setting up available address data and diagnostics"
        )
    except Exception:
        await provider.close()
        raise

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await provider.close()
        raise

    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries and registry state forward."""
    if entry.version > 4:
        _LOGGER.error("Unsupported config entry version: %s", entry.version)
        return False

    if entry.version < 2:
        await _async_migrate_registry_identifiers(hass, entry)
        hass.config_entries.async_update_entry(entry, version=2)

    if entry.version < 3:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_PROVIDER: DEFAULT_PROVIDER_ID}, version=3
        )

    if entry.version < 4:
        hass.config_entries.async_update_entry(
            entry,
            data={CONF_ENTRY_KIND: "address", **entry.data},
            version=4,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a DTEK Monitor config entry."""
    if entry.data.get(CONF_ENTRY_KIND) == ENTRY_ACCOUNT:
        unload_ok = await hass.config_entries.async_unload_platforms(
            entry, ACCOUNT_PLATFORMS
        )
        if unload_ok:
            await entry.runtime_data.client.close()
        return unload_ok
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator: DTEKDataCoordinator = entry.runtime_data
        await coordinator.provider.close()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_migrate_registry_identifiers(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Migrate entity and device registry identifiers from entry_id to unique_id."""
    if not entry.unique_id:
        _LOGGER.warning(
            "Skipping registry migration for %s because unique_id is missing",
            entry.entry_id,
        )
        return

    entity_registry = er.async_get(hass)
    migrated_entities = 0
    for registry_entry in er.async_entries_for_config_entry(
        entity_registry,
        entry.entry_id,
    ):
        new_unique_id = migrate_entity_unique_id(
            registry_entry.unique_id,
            entry.entry_id,
            entry.unique_id,
        )
        if new_unique_id is None or new_unique_id == registry_entry.unique_id:
            continue

        conflict_entity_id = entity_registry.async_get_entity_id(
            registry_entry.domain,
            registry_entry.platform,
            new_unique_id,
        )
        if conflict_entity_id and conflict_entity_id != registry_entry.entity_id:
            conflict_entry = entity_registry.entities.get(conflict_entity_id)
            if (
                conflict_entry is not None
                and conflict_entry.config_entry_id == entry.entry_id
            ):
                _LOGGER.warning(
                    "Removing duplicate migrated entity %s during registry migration",
                    conflict_entity_id,
                )
                entity_registry.async_remove(conflict_entity_id)
            else:
                _LOGGER.warning(
                    "Skipping entity migration for %s because %s already uses %s",
                    registry_entry.entity_id,
                    conflict_entity_id,
                    new_unique_id,
                )
                continue

        entity_registry.async_update_entity(
            registry_entry.entity_id,
            new_unique_id=new_unique_id,
        )
        migrated_entities += 1

    device_registry = dr.async_get(hass)
    migrated_devices = 0
    for device_entry in dr.async_entries_for_config_entry(
        device_registry, entry.entry_id
    ):
        new_identifiers = migrate_device_identifiers(
            device_entry.identifiers,
            DOMAIN,
            entry.entry_id,
            entry.unique_id,
        )
        if new_identifiers is None or new_identifiers == device_entry.identifiers:
            continue

        try:
            device_registry.async_update_device(
                device_entry.id,
                new_identifiers=new_identifiers,
            )
            migrated_devices += 1
        except Exception as err:
            _LOGGER.warning(
                "Failed to migrate device identifiers for %s: %s",
                device_entry.id,
                err,
            )

    if migrated_entities or migrated_devices:
        _LOGGER.info(
            "Migrated DTEK registry identifiers for %s: entities=%d devices=%d",
            entry.entry_id,
            migrated_entities,
            migrated_devices,
        )
