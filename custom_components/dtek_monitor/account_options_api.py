"""Open existing HA options forms for the account selected on a dashboard."""

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.components import websocket_api

from .account_submission import SubmissionError
from .account_submission_api import resolve

ACTIONS = ("group", "reminders", "notices", "checks", "accounts", "monitoring")
CONTEXT_KEY = "dtek_workspace"


@websocket_api.websocket_command(
    {
        vol.Required("type"): "dtek_monitor/account_options",
        vol.Required("entity_id"): str,
        vol.Required("action"): vol.In(ACTIONS),
    }
)
@websocket_api.async_response
async def websocket_options(hass, connection, msg):
    """Resolve on the server; opening a form never saves preferences."""
    try:
        entry, number, _ = resolve(hass, connection, msg["entity_id"])
    except SubmissionError as err:
        connection.send_error(msg["id"], err.code, "Account settings unavailable")
        return
    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        context={CONTEXT_KEY: {"account": number, "action": msg["action"]}},
    )
    try:
        current_entry, current_number, _ = resolve(hass, connection, msg["entity_id"])
        if current_entry is not entry or current_number != number:
            raise SubmissionError("not_available")
    except SubmissionError as err:
        try:
            hass.config_entries.options.async_abort(result["flow_id"])
        except data_entry_flow.UnknownFlow:
            pass
        connection.send_error(msg["id"], err.code, "Account settings unavailable")
        return
    # HA's admin-only options REST API owns serialization, validation and saves.
    connection.send_result(
        msg["id"], {"flow_id": result["flow_id"], "entry_id": entry.entry_id}
    )


async def open_workspace_step(flow, entry, context):
    """Use a fixed allowlist, never a client-supplied method or account number."""
    number, action = context.get("account"), context.get("action")
    if number not in entry.data.get("accounts", {}) or action not in ACTIONS:
        return flow.async_abort(reason="no_accounts")
    if action == "group":
        flow._schedule_account = number
        return await flow.async_step_account_schedule_address()
    if action == "reminders":
        flow._reminder_account = number
        return await flow.async_step_account_reminder_settings()
    if action == "notices":
        flow._notice_account = number
        return await flow.async_step_account_notice_settings()
    if action == "checks":
        flow._check_account = number
        return await flow.async_step_account_check_settings()
    if action == "monitoring":
        return await flow.async_step_account_selection()
    return await flow.async_step_account_management()
