"""Shared helpers for the DTEK Monitor integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .provider import Address
from .providers import CONF_PROVIDER, DEFAULT_PROVIDER_ID

from .const import CONF_CITY, CONF_HOUSE, CONF_STREET, DOMAIN


def entry_identifier(entry: ConfigEntry) -> str:
    """Return a stable identifier for a config entry."""
    return entry.unique_id or entry.entry_id


def build_entity_unique_id(entry: ConfigEntry, suffix: str) -> str:
    """Build a stable entity unique ID from the config entry."""
    return f"{entry_identifier(entry)}_{suffix}"


def build_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Build device info for a DTEK monitored address."""
    city = entry.data[CONF_CITY]
    street = entry.data[CONF_STREET]
    house = entry.data[CONF_HOUSE]

    return DeviceInfo(
        identifiers={(DOMAIN, entry_identifier(entry))},
        name=f"{street}, {house}",
        manufacturer="DTEK",
        model=city,
    )


def suggested_entity_id(entry: ConfigEntry, domain: str, suffix: str) -> str | None:
    """Keep legacy IDs; give new regions one collision-resistant bot prefix.

    HA retains any existing registry ID, including names chosen by the user.
    Device names remain human-readable, independent of this stable prefix.
    """
    provider_id = entry.data.get(CONF_PROVIDER, DEFAULT_PROVIDER_ID)
    if provider_id == DEFAULT_PROVIDER_ID:
        return None
    address = Address(
        provider_id,
        entry.data[CONF_CITY],
        entry.data[CONF_STREET],
        entry.data[CONF_HOUSE],
    )
    suffix = {
        "status": "outage_status",
        "possible_schedule": "possible_outage_schedule",
        "schedule_group": "primary_schedule_group",
        "schedule_groups": "all_schedule_groups",
        "connection": "dtek_connection",
        "last_update": "dtek_last_update",
    }.get(suffix, suffix)
    return f"{domain}.{provider_id}_{address.identity[:16]}_{suffix}"
