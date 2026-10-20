"""HA diagnostics built from technical allowlists, never raw config or API data."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
import re
from typing import TYPE_CHECKING, Any

from .account import CONF_ACCOUNTS, CONF_ENTRY_KIND, ENTRY_ACCOUNT
from .providers import CONF_PROVIDER, DEFAULT_PROVIDER_ID, PROVIDERS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


def _choice(value, choices):
    return value if isinstance(value, str) and value in choices else None


def _integer(value):
    return value if type(value) is int and value >= 0 else None


def _boolean(value):
    return value if type(value) is bool else None


def _timestamp(value):
    return value.isoformat() if isinstance(value, datetime) else None


def _count(value):
    return len(value) if isinstance(value, (Mapping, list, tuple)) else None


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry,
) -> dict[str, Any]:
    """Describe cached health without fetching data or exporting identities.

    HA adds its system information and integration manifest to the download.
    Explicit allowlists also protect future config fields and account-keyed maps;
    generic key redaction would miss titles, identifiers and exception messages.
    """
    kind = _choice(entry.data.get(CONF_ENTRY_KIND, "address"), ("address", ENTRY_ACCOUNT))
    coordinator = getattr(entry, "runtime_data", None)
    result = {
        "schema_version": 1,
        "entry": {
            "kind": kind,
            "provider_id": _choice(entry.data.get(CONF_PROVIDER, DEFAULT_PROVIDER_ID), PROVIDERS),
            "config_version": _integer(getattr(entry, "version", None)),
            "config_minor_version": _integer(getattr(entry, "minor_version", None)),
            "state": _choice(getattr(getattr(entry, "state", None), "value", None), (
                "loaded", "setup_error", "migration_error", "setup_retry",
                "not_loaded", "failed_unload", "setup_in_progress", "unload_in_progress",
            )),
        },
        "runtime": {"available": coordinator is not None},
    }
    if kind == ENTRY_ACCOUNT:
        result["accounts"] = _account_diagnostics(entry, coordinator)
    elif kind == "address":
        gates = _mapping(getattr(hass, "data", {}).get("dtek_monitor_public_requests"))
        gate = gates.get(result["entry"]["provider_id"])
        result["public_requests"] = {
            "retry_in_seconds": _integer(getattr(gate, "retry_in", None)),
            "consecutive_failures": _integer(getattr(gate, "failures", None)),
        }
    if coordinator is None:
        return result

    interval = getattr(coordinator, "update_interval", None)
    result["runtime"].update({
        "last_update_success": _boolean(getattr(coordinator, "last_update_success", None)),
        "update_interval_seconds": interval.total_seconds() if isinstance(interval, timedelta) else None,
        "has_update_error": getattr(coordinator, "last_exception", None) is not None,
    })
    if kind == "address":
        result["address"] = _address_diagnostics(coordinator)
    return result


def _address_diagnostics(coordinator):
    schedule = getattr(coordinator, "schedule", None)
    snapshot = getattr(coordinator, "snapshot", None)
    publication = getattr(schedule, "publication", None)
    transport = _mapping(getattr(getattr(coordinator, "provider", None), "diagnostics", None))
    browser = transport.get("browser_profile")
    return {
        "connection_state": _choice(getattr(coordinator, "connection_state", None), ("ok", "error")),
        "schedule_state": _choice(getattr(coordinator, "schedule_state", None), (
            "current", "missing", "unpublished", "unsupported",
        )),
        "address_success": _boolean(getattr(coordinator, "address_success", None)),
        "notifications_ready": _boolean(getattr(coordinator, "notifications_ready", None)),
        "consecutive_failures": _integer(getattr(coordinator, "consecutive_failures", None)),
        "last_success": _timestamp(getattr(coordinator, "last_success", None)),
        "address_last_success": _timestamp(getattr(coordinator, "address_last_success", None)),
        "schedule_last_success": _timestamp(getattr(coordinator, "schedule_last_success", None)),
        "errors": {
            field: getattr(coordinator, field, None) is not None
            for field in ("address_error", "schedule_error", "snapshot_storage_error")
        },
        "group_count": _count(_mapping(getattr(coordinator, "address_data", None)).get("schedule_groups")),
        "repairs": {
            "interval_count": _count(getattr(coordinator, "address_works", None)),
            "incomplete": _boolean(getattr(coordinator, "works_incomplete", None)),
        },
        "schedule": {
            "available": schedule is not None,
            "group_count": _count(getattr(schedule, "groups", None)),
            "interval_count": _count(getattr(schedule, "intervals", None)),
            "coverage_count": _count(getattr(schedule, "coverage", None)),
            "fetched_at": _timestamp(getattr(schedule, "fetched_at", None)),
            "source_updated_at": _timestamp(getattr(schedule, "source_updated_at", None)),
            "publication": {
                field: _boolean(getattr(publication, field, None))
                for field in ("schedule", "daily", "weekly", "group_labels")
            },
        },
        "snapshot": {
            "available": snapshot is not None,
            "checked_at": _timestamp(getattr(snapshot, "checked_at", None)),
            "coverage_start": _timestamp(getattr(snapshot, "coverage_start", None)),
            "coverage_end": _timestamp(getattr(snapshot, "coverage_end", None)),
            "group_count": _count(getattr(snapshot, "groups", None)),
            "interval_count": _count(getattr(snapshot, "windows", None)),
        },
        "transport": {
            **{key: _integer(transport.get(key)) for key in (
                "transport_errors", "protection_challenges", "protection_blocks",
                "public_retry_in_seconds", "public_consecutive_failures",
            )},
            "browser_profile": browser if isinstance(browser, str) and re.fullmatch(r"chrome[0-9]{2,3}", browser) else None,
        },
    }


def _account_diagnostics(entry, coordinator):
    selected = _mapping(entry.data.get(CONF_ACCOUNTS))
    data = _mapping(getattr(coordinator, "data", None))
    links = _mapping(entry.options.get("account_schedules"))
    building_states = _mapping(getattr(getattr(coordinator, "buildings", None), "states", None))
    # List positions identify entries only within this export; no account number,
    # suffix, hash, address or HA entity/entry identifier leaves this function.
    accounts = []
    for number in selected:
        snapshot = data.get(number)
        link = _mapping(links.get(number))
        manager = getattr(coordinator, "groups", None)
        cabinet = manager.get(number) if manager is not None else None
        accounts.append({
            "cached_data_present": snapshot is not None,
            "fetched_at": _timestamp(getattr(snapshot, "fetched_at", None)),
            "reading_zone_count": _count(getattr(snapshot, "readings", None)),
            "profile_present": getattr(snapshot, "profile", None) is not None,
            "unavailable_services": [
                name for name in ("meter_details", "meter_readings", "meter_consumption")
                if name in getattr(snapshot, "unavailable", ())
            ],
            "building_link_configured": bool(link.get("address_entry_id")),
            "selected_group_count": _count(link.get("groups", [])),
            "cabinet_group_status": _choice(getattr(cabinet, "status", None), (
                "current", "empty", "pending", "expired", "unavailable",
                "api_changed", "disabled", "unsupported",
            )),
            "cabinet_group_count": _count(getattr(cabinet, "groups", None)),
            "cabinet_groups_checked_at": _timestamp(getattr(cabinet, "checked_at", None)),
            "building_match_state": _choice(building_states.get(number), (
                "address_pending", "address_confirmation", "address_lookup_unavailable",
            )),
        })
    return {
        "selected_count": len(selected), "items": accounts,
        "meter_auth_format": _choice(
            getattr(getattr(coordinator, "client", None), "_meter_auth", None),
            ("raw", "bearer"),
        ),
    }
