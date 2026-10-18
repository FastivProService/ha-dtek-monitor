"""Authenticated history access, scoped to a readable entity of one account."""

from pathlib import Path
from datetime import datetime

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import callback
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
from .account_profile import public_profile
from .account_analysis import compare_consumption, match_readback
from .account_history import KYIV
from .account_submission import SubmissionError


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
    from .account_submission_api import async_setup_submissions
    from .account_energy_api import async_setup_energy

    await async_setup_submissions(hass)
    await async_setup_energy(hass)
    websocket_api.async_register_command(hass, websocket_history)
    websocket_api.async_register_command(hass, websocket_identity)
    websocket_api.async_register_command(hass, websocket_accounts)
    websocket_api.async_register_command(hass, websocket_checks)
    websocket_api.async_register_command(hass, websocket_profile)
    websocket_api.async_register_command(hass, websocket_readback)
    if "http" in hass.config.components:
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    "/dtek_monitor/account-profile-card.js",
                    str(Path(__file__).parent / "frontend" / "account-profile-card.js"),
                    False,
                ),
                StaticPathConfig(
                    "/dtek_monitor/account-check-card.js",
                    str(Path(__file__).parent / "frontend" / "account-check-card.js"),
                    False,
                ),
                StaticPathConfig(
                    "/dtek_monitor/account-card-editor.js",
                    str(Path(__file__).parent / "frontend" / "account-card-editor.js"),
                    False,
                ),
                StaticPathConfig(
                    "/dtek_monitor/schedule-card.js",
                    str(Path(__file__).parent / "frontend" / "schedule-card.js"),
                    False,
                ),
                StaticPathConfig(
                    "/dtek_monitor/account-history-card.js",
                    str(Path(__file__).parent / "frontend" / "account-history-card.js"),
                    False,
                ),
                StaticPathConfig(
                    "/dtek_monitor/account-number.js",
                    str(Path(__file__).parent / "frontend" / "account-number.js"),
                    False,
                ),
            ]
        )


class AccountAccessError(Exception):
    def __init__(self, code):
        self.code = code


@websocket_api.websocket_command({vol.Required("type"): "dtek_monitor/accounts"})
@callback
def websocket_accounts(hass, connection, msg):
    """Local choices for cards, scoped to selected, readable account devices."""
    result = []
    registry = er.async_get(hass)
    for entry in hass.config_entries.async_entries(DOMAIN):
        if (
            entry.data.get(CONF_ENTRY_KIND) != ENTRY_ACCOUNT
            or entry.state != ConfigEntryState.LOADED
        ):
            continue
        entities = er.async_entries_for_config_entry(registry, entry.entry_id)
        for number, address in entry.data[CONF_ACCOUNTS].items():
            prefix = f"account:{account_key(entry.data[CONF_PROVIDER], number)}:"
            entity = next(
                (
                    e
                    for e in entities
                    if e.platform == DOMAIN
                    and e.disabled_by is None
                    and e.unique_id == prefix + "account_updated:all"
                ),
                None,
            )
            if entity is not None and _can_read_account(
                hass, connection, entry, number
            ):
                result.append(
                    {
                        "entity_id": entity.entity_id,
                        "masked": f"••••{number[-4:]}",
                        "address": address,
                        # Preserve manually configured entities for this account.
                        "entities": [
                            e.entity_id
                            for e in entities
                            if e.platform == DOMAIN
                            and e.disabled_by is None
                            and e.unique_id.startswith(prefix)
                            and connection.user.permissions.check_entity(
                                e.entity_id, POLICY_READ
                            )
                        ],
                    }
                )
    connection.send_result(msg["id"], result)


def resolve_readable_account(hass, connection, entity_id):
    if not connection.user.permissions.check_entity(entity_id, POLICY_READ):
        raise AccountAccessError("unauthorized")
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
        raise AccountAccessError("not_available")
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
        raise AccountAccessError("not_available")
    if not _can_read_account(hass, connection, entry, number):
        raise AccountAccessError("unauthorized")
    return entry, number, entity


@websocket_api.websocket_command(
    {
        vol.Required("type"): "dtek_monitor/account_identity",
        vol.Required("entity_id"): str,
        vol.Optional("reveal", default=False): bool,
    }
)
@callback
def websocket_identity(hass, connection, msg):
    # Local registry/config data only: no DTEK request and no state attributes.
    try:
        _, number, _ = resolve_readable_account(hass, connection, msg["entity_id"])
    except AccountAccessError as error:
        connection.send_error(msg["id"], error.code, "Personal account is unavailable")
        return
    result = {"masked": f"••••{number[-4:]}"}
    if msg["reveal"]:
        result["number"] = number
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "dtek_monitor/account_checks",
        vol.Required("entity_id"): str,
    }
)
@callback
def websocket_checks(hass, connection, msg):
    """Read the local journal only; opening a dashboard never runs a check."""
    try:
        entry, number, _ = resolve_readable_account(hass, connection, msg["entity_id"])
    except AccountAccessError as error:
        connection.send_error(msg["id"], error.code, "Personal account is unavailable")
        return
    connection.send_result(msg["id"], entry.runtime_data.checks.view(number))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "dtek_monitor/account_history",
        vol.Required("entity_id"): str,
        vol.Required("year"): int,
        vol.Optional("force", default=False): bool,
        vol.Optional("compare", default=False): bool,
    }
)
@websocket_api.async_response
async def websocket_history(hass, connection, msg):
    entity_id = msg["entity_id"]
    try:
        entry, number, entity = resolve_readable_account(hass, connection, entity_id)
    except AccountAccessError as error:
        connection.send_error(msg["id"], error.code, "Personal account is unavailable")
        return
    coordinator = entry.runtime_data
    try:
        result = await coordinator.history.get(number, msg["year"], force=msg["force"])
        if msg.get("compare") and msg["year"] > 2000:
            previous = await coordinator.history.get(
                number, msg["year"] - 1, force=msg["force"]
            )
            result = {**result, "comparison": compare_consumption(result, previous)}
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): "dtek_monitor/account_profile",
        vol.Required("entity_id"): str,
        vol.Optional("reveal", default=False): bool,
    }
)
@callback
def websocket_profile(hass, connection, msg):
    """Cached profile remains independent of the ability to submit readings."""
    try:
        entry, number, _ = resolve_readable_account(hass, connection, msg["entity_id"])
    except AccountAccessError as error:
        connection.send_error(msg["id"], error.code, "Personal account is unavailable")
        return
    coordinator = entry.runtime_data
    snapshot = (coordinator.data or {}).get(number)
    profile = snapshot.profile if snapshot and snapshot.profile else {}
    result = public_profile(profile, reveal=msg["reveal"])
    result["address"] = result.get("address") or entry.data[CONF_ACCOUNTS][number]
    result["fetched_at"] = snapshot.fetched_at.isoformat() if snapshot else None
    result["available"] = bool(snapshot and coordinator.last_update_success)
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "dtek_monitor/account_readback",
        vol.Required("entity_id"): str,
    }
)
@websocket_api.async_response
async def websocket_readback(hass, connection, msg):
    entry = None
    try:
        entry, number, entity = resolve_readable_account(
            hass, connection, msg["entity_id"]
        )
        coordinator = entry.runtime_data
        attempt = await coordinator.submissions.last_attempt(number)
        try:
            year = (
                datetime.fromisoformat(attempt["at"]).astimezone(KYIV).year
                if attempt
                else datetime.now(KYIV).year
            )
        except (TypeError, ValueError, KeyError):
            raise AccountError("Invalid attempt date") from None
        history = await coordinator.history.get(number, year, force=True)
        current, selected, current_entity = resolve_readable_account(
            hass, connection, msg["entity_id"]
        )
        if (
            current is not entry
            or selected != number
            or current.runtime_data is not coordinator
            or current_entity.unique_id != entity.unique_id
        ):
            raise AccountAccessError("not_available")
        snapshot = (coordinator.data or {}).get(number)
        serial = (snapshot.profile or {}).get("serial") if snapshot else None
        connection.send_result(msg["id"], match_readback(attempt, history, serial))
    except AccountAccessError as error:
        connection.send_error(msg["id"], error.code, "Personal account is unavailable")
    except AccountAuthError:
        if entry:
            entry.async_start_reauth(hass)
        connection.send_error(
            msg["id"], "auth_required", "Personal account authentication required"
        )
    except AccountRateLimitError:
        connection.send_error(
            msg["id"], "rate_limited", "Please wait before refreshing history again"
        )
    except (AccountError, SubmissionError):
        connection.send_error(
            msg["id"], "history_unavailable", "History could not be verified"
        )
