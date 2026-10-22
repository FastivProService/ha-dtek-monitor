"""Administrator-only, account-scoped commands for explicit reading submission."""

from pathlib import Path

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from .account import (
    CONF_ACCOUNTS,
    CONF_ENTRY_KIND,
    ENTRY_ACCOUNT,
    AccountAPIChanged,
    AccountAuthError,
    AccountError,
    AccountRateLimitError,
    account_key,
)
from .account_submission import SubmissionError
from .account_photos import MAX_PHOTO_ENCODED
from .const import DOMAIN
from .providers import CONF_PROVIDER


def resolve(hass, connection, entity_id):
    user = connection.user
    if not user.is_admin or not user.is_active:
        raise SubmissionError("unauthorized")
    entity = er.async_get(hass).async_get(entity_id)
    entry = (
        hass.config_entries.async_get_entry(entity.config_entry_id) if entity else None
    )
    if (
        not entity
        or entity.platform != DOMAIN
        or entity.disabled_by is not None
        or not entry
        or entry.domain != DOMAIN
        or entry.state != ConfigEntryState.LOADED
        or entry.data.get(CONF_ENTRY_KIND) != ENTRY_ACCOUNT
    ):
        raise SubmissionError("not_available")
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
        raise SubmissionError("not_available")
    prefix = f"account:{account_key(entry.data[CONF_PROVIDER], number)}:"
    entities = [
        e
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.platform == DOMAIN and e.unique_id.startswith(prefix)
    ]
    if not entities or any(
        not user.permissions.check_entity(e.entity_id, policy)
        for e in [entity, *entities]
        for policy in (POLICY_READ, POLICY_CONTROL)
    ):
        raise SubmissionError("unauthorized")
    return entry, number, entry.runtime_data


async def async_setup_submissions(hass):
    websocket_api.async_register_command(hass, websocket_submission)
    if "http" in hass.config.components:
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    "/dtek_monitor/account-readings-card.js",
                    str(
                        Path(__file__).parent / "frontend" / "account-readings-card.js"
                    ),
                    False,
                )
            ]
        )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "dtek_monitor/account_submission",
        vol.Required("entity_id"): str,
        vol.Required("action"): vol.In(
            ("context", "prepare", "confirm", "acknowledge")
        ),
        vol.Optional("revision"): vol.All(str, vol.Length(max=64)),
        vol.Optional("values"): {
            vol.All(str, vol.Length(max=40)): vol.All(str, vol.Length(max=8))
        },
        vol.Optional("confirmation"): vol.All(str, vol.Length(max=64)),
        vol.Optional("attempt"): vol.All(str, vol.Length(max=64)),
        vol.Optional("source_token"): vol.All(str, vol.Length(max=64)),
        vol.Optional("photos"): vol.All({
            vol.All(str, vol.Length(max=40)): {
                vol.Required("data"): vol.All(str, vol.Length(max=MAX_PHOTO_ENCODED)),
                vol.Required("mime_type"): vol.In(("image/jpeg", "image/png")),
            },
        }, vol.Length(max=2)),
    }
)
@websocket_api.async_response
async def websocket_submission(hass, connection, msg):
    entry = None
    try:
        entry, number, coordinator = resolve(hass, connection, msg["entity_id"])

        def authorize(source=None):
            current, selected, runtime = resolve(hass, connection, msg["entity_id"])
            if current is not entry or selected != number or runtime is not coordinator:
                raise SubmissionError("not_available")
            if source and not connection.user.permissions.check_entity(
                source, POLICY_READ
            ):
                raise SubmissionError("unauthorized")

        manager = coordinator.submissions
        action = msg["action"]
        if action == "context":
            result = await manager.context(number, authorize)
            authorize()
            result.update(account=f"••••{number[-4:]}")
            result.setdefault("address", entry.data[CONF_ACCOUNTS][number])
        elif action == "prepare":
            result = await manager.prepare(
                number,
                connection.user.id,
                msg.get("revision"),
                msg.get("values"),
                authorize,
                source_token=msg.get("source_token"),
                photos=msg.get("photos"),
            )
        elif action == "confirm":
            result = await manager.confirm(
                number, connection.user.id, msg.get("confirmation"), authorize
            )
            # Do not let a cached history look like a post-submission verification.
            coordinator.history.invalidate(number)
        else:
            result = await manager.acknowledge(number, msg.get("attempt"), authorize)
        authorize()
        connection.send_result(msg["id"], result)
    except SubmissionError as error:
        connection.send_error(
            msg["id"], error.code, "Reading operation could not be completed"
        )
    except AccountAPIChanged:
        connection.send_error(
            msg["id"], "api_changed", "DTEK service is incompatible; check HA Repairs"
        )
        return
    except AccountAuthError:
        if entry:
            entry.async_start_reauth(hass)
        connection.send_error(
            msg["id"], "auth_required", "Personal account authentication required"
        )
    except AccountRateLimitError:
        connection.send_error(
            msg["id"], "rate_limited", "Please wait before trying again"
        )
    except AccountError:
        connection.send_error(
            msg["id"], "unavailable", "Personal account service is unavailable"
        )
