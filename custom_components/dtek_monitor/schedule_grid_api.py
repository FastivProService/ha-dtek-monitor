"""Local schedule grid access scoped to an authorized calendar and status entity."""

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from datetime import datetime, timezone
from homeassistant.helpers import entity_registry as er

from .account import CONF_ENTRY_KIND, ENTRY_ACCOUNT
from .account_history_api import AccountAccessError, resolve_readable_account
from .account_schedule import SELECTED_STATUSES, selection
from .const import DOMAIN
from .helpers import build_entity_unique_id
from .schedule_grid import grid_data
from .schedule_changes import changed_groups, schedule_record
from .schedule_status import schedule_status


def resolve_grid(hass, connection, msg):
    entity = er.async_get(hass).async_get(msg["entity_id"])
    entry = (
        hass.config_entries.async_get_entry(entity.config_entry_id)
        if entity
        else None
    )
    if not connection.user.permissions.check_entity(msg["entity_id"], POLICY_READ):
        raise AccountAccessError("unauthorized")
    if (
        not entity
        or entity.platform != DOMAIN
        or entity.disabled_by is not None
        or not entry
        or entry.domain != DOMAIN
        or entry.state != ConfigEntryState.LOADED
    ):
        raise AccountAccessError("not_available")
    if entry.data.get(CONF_ENTRY_KIND) == ENTRY_ACCOUNT:
        entry, number, entity = resolve_readable_account(
            hass, connection, msg["entity_id"]
        )
        if not entity.unique_id.endswith(":account_schedule_status:all"):
            raise AccountAccessError("not_available")
        address, coordinator, groups, status = selection(hass, entry, number)
        if coordinator is None:
            return None, (), status
        # The public grid is operator-wide. Personal selection only highlights.
        selected = groups if status in SELECTED_STATUSES else ()
        target, suffix = (
            entry,
            entity.unique_id.removesuffix("account_schedule_status:all"),
        )
        required = {
            suffix + "account_outage_schedule:all",
            suffix + "account_possible_schedule:all",
        }
    else:
        if entity.unique_id != build_entity_unique_id(entry, "schedule_status"):
            raise AccountAccessError("not_available")
        coordinator, selected, target = entry.runtime_data, (), entry
        required = {
            build_entity_unique_id(entry, key)
            for key in ("outage_schedule", "possible_schedule")
        }
    readable = {
        e.unique_id
        for e in er.async_entries_for_config_entry(
            er.async_get(hass), target.entry_id
        )
        if e.platform == DOMAIN
        and e.disabled_by is None
        and connection.user.permissions.check_entity(e.entity_id, POLICY_READ)
    }
    if not required.issubset(readable):
        raise AccountAccessError("unauthorized")
    return coordinator, selected, None


@websocket_api.websocket_command(
    {vol.Required("type"): "dtek_monitor/schedule_grid", vol.Required("entity_id"): str}
)
@callback
def websocket_grid(hass, connection, msg):
    try:
        coordinator, selected, status = resolve_grid(hass, connection, msg)
        connection.send_result(msg["id"], grid_data(coordinator, selected) if coordinator else {"status": status, "days": []})
    except AccountAccessError as error:
        connection.send_error(msg["id"], error.code, "Schedule grid is unavailable")


@websocket_api.websocket_command(
    {vol.Required("type"): "dtek_monitor/schedule_refresh", vol.Required("entity_id"): str}
)
@websocket_api.async_response
async def websocket_refresh(hass, connection, msg):
    try:
        if not connection.user.permissions.check_entity(msg["entity_id"], POLICY_CONTROL):
            raise AccountAccessError("unauthorized")
        coordinator, _, _ = resolve_grid(hass, connection, msg)
        if coordinator is None:
            raise AccountAccessError("not_available")
        previous = schedule_record(coordinator.schedule)
        await coordinator.async_manual_refresh()
        # Permissions or the selected building may change while awaiting DTEK.
        current, _, _ = resolve_grid(hass, connection, msg)
        if current is not coordinator or not connection.user.permissions.check_entity(msg["entity_id"], POLICY_CONTROL):
            raise AccountAccessError("not_available")
        connection.send_result(msg["id"], {
            "status": schedule_status(coordinator),
            "changed": bool(changed_groups(previous, schedule_record(coordinator.schedule), datetime.now(timezone.utc))),
        })
    except AccountAccessError as error:
        connection.send_error(msg["id"], error.code, "Schedule refresh is unavailable")
    except HomeAssistantError as error:
        key = error.translation_key or "schedule_refresh_failed"
        connection.send_error(msg["id"], key, "Schedule refresh failed",
                              translation_key=key, translation_domain=DOMAIN,
                              translation_placeholders=error.translation_placeholders)
