"""Authenticated history access, scoped to a readable entity of one account."""

from pathlib import Path

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from .account import (
    CONF_ACCOUNTS,
    CONF_ENTRY_KIND,
    ENTRY_ACCOUNT,
    AccountAuthError,
    AccountError,
    AccountRateLimitError,
    account_key,
)
from .const import DOMAIN
from .providers import CONF_PROVIDER


def _can_read_account(hass, connection, entry, number):
    prefix = f"account:{account_key(entry.data[CONF_PROVIDER], number)}:"
    entities = [
        entity
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if entity.platform == DOMAIN
        and entity.entity_id.startswith("sensor.")
        and entity.unique_id.startswith(prefix)
    ]
    # Read access to a timestamp or model alone must not unlock billing history.
    return bool(entities) and all(
        connection.user.permissions.check_entity(entity.entity_id, POLICY_READ)
        for entity in entities
    )


async def async_setup_history(hass):
    websocket_api.async_register_command(hass, websocket_history)
    if "http" in hass.config.components:
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    "/dtek_monitor/account-history-card.js",
                    str(Path(__file__).parent / "frontend" / "account-history-card.js"),
                    False,
                )
            ]
        )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "dtek_monitor/account_history",
        vol.Required("entity_id"): str,
        vol.Required("year"): int,
        vol.Optional("force", default=False): bool,
    }
)
@websocket_api.async_response
async def websocket_history(hass, connection, msg):
    entity_id = msg["entity_id"]
    if not connection.user.permissions.check_entity(entity_id, POLICY_READ):
        connection.send_error(msg["id"], "unauthorized", "History access denied")
        return
    entity = er.async_get(hass).async_get(entity_id)
    entry = (
        hass.config_entries.async_get_entry(entity.config_entry_id) if entity else None
    )
    if (
        entity is None
        or entity.platform != DOMAIN
        or entity.disabled_by is not None
        or entry is None
        or entry.domain != DOMAIN
        or entry.data.get(CONF_ENTRY_KIND) != ENTRY_ACCOUNT
        or entry.state != ConfigEntryState.LOADED
    ):
        connection.send_error(
            msg["id"], "not_available", "Personal account is unavailable"
        )
        return
    number = next(
        (
            n
            for n in entry.data[CONF_ACCOUNTS]
            if entity.unique_id.startswith(
                f"account:{account_key(entry.data[CONF_PROVIDER], n)}:"
            )
        ),
        None,
    )
    if number is None:
        connection.send_error(
            msg["id"], "not_available", "Personal account is unavailable"
        )
        return
    if not _can_read_account(hass, connection, entry, number):
        connection.send_error(msg["id"], "unauthorized", "History access denied")
        return
    coordinator = entry.runtime_data
    try:
        result = await coordinator.history.get(number, msg["year"], force=msg["force"])
    except AccountAuthError:
        entry.async_start_reauth(hass)
        connection.send_error(
            msg["id"], "auth_required", "Personal account authentication required"
        )
        return
    except AccountRateLimitError:
        connection.send_error(
            msg["id"], "rate_limited", "Please wait before refreshing history again"
        )
        return
    except AccountError:
        connection.send_error(
            msg["id"], "history_unavailable", "History could not be verified"
        )
        return
    # Removal/reload or permission changes during a request must revoke its result too.
    current_entity = er.async_get(hass).async_get(entity_id)
    if (
        entry.state != ConfigEntryState.LOADED
        or entry.runtime_data is not coordinator
        or number not in entry.data[CONF_ACCOUNTS]
        or not connection.user.permissions.check_entity(entity_id, POLICY_READ)
        or current_entity is None
        or current_entity.unique_id != entity.unique_id
        or current_entity.config_entry_id != entry.entry_id
        or current_entity.disabled_by is not None
        or not _can_read_account(hass, connection, entry, number)
    ):
        connection.send_error(
            msg["id"], "not_available", "Personal account is unavailable"
        )
        return
    connection.send_result(msg["id"], result)
