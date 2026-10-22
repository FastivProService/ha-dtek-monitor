"""Protected document listing/download; no public URLs or files in /local."""
import voluptuous as vol
from homeassistant.components import websocket_api
from .account import AccountAPIChanged, AccountError
from .account_history_api import AccountAccessError, resolve_readable_account
from .account_submission import SubmissionError


@websocket_api.websocket_command({
    vol.Required("type"): "dtek_monitor/account_documents",
    vol.Required("entity_id"): str,
    vol.Optional("document_id"): vol.All(str, vol.Match(r"^[a-f0-9]{32}$")),
})
@websocket_api.async_response
async def websocket_documents(hass, connection, msg):
    try:
        entry, number, _ = resolve_readable_account(hass, connection, msg["entity_id"])
        manager = entry.runtime_data.documents
        result = (await manager.download(number, msg["document_id"]) if msg.get("document_id")
                  else await manager.get(number))
        current, selected, _ = resolve_readable_account(hass, connection, msg["entity_id"])
        if current is not entry or selected != number or current.runtime_data.documents is not manager:
            raise SubmissionError("not_available")
        connection.send_result(msg["id"], result)
    except (AccountAccessError, SubmissionError) as error:
        connection.send_error(msg["id"], error.code, "Document unavailable")
    except AccountAPIChanged:
        connection.send_error(msg["id"], "api_changed", "Document service incompatible; check HA Repairs")
    except AccountError:
        connection.send_error(msg["id"], "unavailable", "Document service unavailable")
