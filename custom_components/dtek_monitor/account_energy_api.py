"""HA energy-source adapter and administrator-only local reference operations."""

from decimal import Decimal, DecimalException
from pathlib import Path
import logging

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er

from .account import AccountAuthError, AccountError, AccountRateLimitError
from .account_energy import EnergyManager
from .account_submission import SubmissionError, digest
from .account_submission_api import resolve
from .account_submission_store import SubmissionStore
from .providers import CONF_PROVIDER

_LOGGER = logging.getLogger(__name__)


def source_snapshot(hass, entity_id, state=None):
    entity = er.async_get(hass).async_get(entity_id)
    state = state if state is not None else hass.states.get(entity_id)
    if (
        not entity_id.startswith("sensor.")
        or not entity
        or entity.disabled_by
        or entity.platform == "dtek_monitor"
        or state is None
        or state.attributes.get("device_class") != "energy"
        or state.attributes.get("state_class") not in ("total", "total_increasing")
    ):
        raise SubmissionError("invalid_energy_source")
    unit = state.attributes.get("unit_of_measurement")
    if unit not in ("Wh", "kWh", "MWh"):
        raise SubmissionError("invalid_energy_source")
    if state.attributes.get("restored") is True:
        raise SubmissionError("source_unavailable")
    try:
        value = Decimal(state.state)
        if (
            not value.is_finite()
            or value < 0
            or not -6 <= value.as_tuple().exponent <= 12
            or value >= 10**12
        ):
            raise ValueError
        value *= {"Wh": Decimal("0.001"), "kWh": Decimal(1), "MWh": Decimal(1000)}[unit]
        if value >= 10**12:
            raise ValueError
    except (DecimalException, ValueError):
        raise SubmissionError("source_unavailable") from None
    # last_updated includes attribute reports, unlike last_changed. It is a HA
    # observation timestamp, not proof of an upstream device measurement.
    return {
        "value": format(value.normalize(), "f"),
        "unit": unit,
        "at": state.last_updated.isoformat(),
        "fingerprint": digest(
            [entity.platform, entity.unique_id, entity.config_entry_id]
        ),
    }


class HAEnergyManager(EnergyManager):
    def __init__(self, hass, entry):
        super().__init__(
            SubmissionStore(
                hass.config.path(".storage", f"dtek_monitor.energy.{entry.entry_id}"),
                hass.async_add_executor_job,
            ),
            entry.data[CONF_PROVIDER],
            lambda entity_id: source_snapshot(hass, entity_id),
        )
        self.hass = hass
        self._unsubscribe = None

    async def start(self):
        try:
            await super().start()
        except SubmissionError:
            # Broken local references must not disable cabinet/address monitoring.
            _LOGGER.error(
                "Local energy references could not be loaded; source proposals are disabled"
            )
            return

        @callback
        def changed(event):
            entity_id = event.data["entity_id"]
            state = event.data.get("new_state")
            if state is None or not self.uses(entity_id):
                return
            try:
                sample = source_snapshot(self.hass, entity_id, state)
            except SubmissionError:
                return
            self.hass.async_create_task(self._observe_safely(entity_id, sample))

        self._unsubscribe = self.hass.bus.async_listen(EVENT_STATE_CHANGED, changed)

    async def _observe_safely(self, entity_id, sample):
        try:
            await self.observe(entity_id, sample)
        except SubmissionError:
            if not self._closed:
                _LOGGER.error(
                    "Local energy observation could not be saved; source proposals are disabled"
                )

    async def close(self):
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        await super().close()


async def async_setup_energy(hass):
    websocket_api.async_register_command(hass, websocket_energy)
    if "http" in hass.config.components:
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    "/dtek_monitor/account-energy-editor.js",
                    str(
                        Path(__file__).parent / "frontend" / "account-energy-editor.js"
                    ),
                    False,
                )
            ]
        )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "dtek_monitor/account_energy",
        vol.Required("entity_id"): str,
        vol.Required("action"): vol.In(("get", "preview", "save", "remove")),
        vol.Optional("revision"): vol.Any(None, vol.All(str, vol.Length(max=64))),
        vol.Optional("config"): {
            vol.Required("mode"): vol.In(("direct", "offset")),
            vol.Required("entities"): {
                vol.In(("GENERAL", "DAY", "NIGHT")): vol.All(str, vol.Length(max=255))
            },
            vol.Required("max_age"): vol.All(int, vol.Range(min=60, max=86400)),
            vol.Required("max_power_kw"): vol.All(str, vol.Length(max=16)),
        },
        vol.Optional("references"): {
            vol.In(("GENERAL", "DAY", "NIGHT")): vol.All(str, vol.Length(max=24))
        },
        vol.Optional("confirmation"): vol.All(str, vol.Length(max=64)),
    }
)
@websocket_api.async_response
async def websocket_energy(hass, connection, msg):
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

        manager, user = coordinator.energy, connection.user.id
        action = msg["action"]
        if action == "get":
            result = await manager.get(number, user, authorize)
        elif action == "preview":
            # A calibration of an existing reference is local and works even
            # when DTEK is offline or currently blocks additional submissions.
            context = None
            if "config" in msg:
                context = await coordinator.client.read_submission_context(number)
                authorize()
            result = await manager.preview(
                number,
                user,
                context,
                msg.get("config"),
                msg.get("references"),
                msg.get("revision"),
                authorize,
            )
        elif action == "save":
            result = await manager.save(
                number, user, msg.get("confirmation"), authorize
            )
        else:
            result = await manager.remove(number, msg.get("revision"), authorize)
        authorize()
        connection.send_result(msg["id"], result)
    except SubmissionError as error:
        connection.send_error(
            msg["id"], error.code, "Local energy operation could not be completed"
        )
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
