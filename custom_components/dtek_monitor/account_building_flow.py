"""Confirm an ambiguous building inside the account's existing options dialog."""

from copy import deepcopy

import voluptuous as vol
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .account import CONF_ACCOUNTS
from .account_buildings import LINKS, ensure_building
from .building import building_suggestion, unique_choice, verify_building
from .const import CONF_CITY, CONF_HOUSE, CONF_STREET
from .provider import ProviderError
from .providers import CONF_PROVIDER, create_provider


def begin(flow, entry):
    number = flow._schedule_account
    if number not in entry.data[CONF_ACCOUNTS]:
        return False
    flow._building_origin = (
        number,
        entry.data[CONF_ACCOUNTS][number],
        deepcopy(entry.options.get(LINKS, {}).get(number)),
    )
    flow._building_suggestion = building_suggestion(flow._building_origin[1])
    flow._building_values = {}
    flow._building_choices = {}
    return True


def active(flow, entry):
    number, text, old = flow._building_origin
    return (
        flow.hass.config_entries.async_get_entry(entry.entry_id) is entry
        and entry.data.get(CONF_ACCOUNTS, {}).get(number) == text
        and entry.options.get(LINKS, {}).get(number) == old
    )


async def configure(flow, entry, key, user_input):
    if not active(flow, entry):
        return flow.async_abort(reason="building_context_changed")
    errors = {}
    choices = flow._building_choices.get(key)
    if choices is None:
        provider = create_provider(entry.data[CONF_PROVIDER], hass=flow.hass)
        try:
            if key == CONF_CITY:
                choices = await provider.list_localities()
            elif key == CONF_STREET:
                choices = await provider.list_streets(flow._building_values[CONF_CITY])
            else:
                choices = await provider.list_houses(
                    flow._building_values[CONF_CITY], flow._building_values[CONF_STREET]
                )
            if choices:
                flow._building_choices[key] = list(choices)
            else:
                errors["base"] = "building_not_found"
        except ProviderError:
            errors["base"] = "cannot_connect"
            choices = []
        finally:
            await provider.close()
    if user_input is not None and not errors:
        value = user_input.get(key)
        if not isinstance(value, str) or value not in choices:
            errors["base"] = "invalid_building"
        else:
            flow._building_values[key] = value
            if key == CONF_CITY:
                flow._building_choices.pop(CONF_STREET, None)
                return await flow.async_step_account_building_street()
            if key == CONF_STREET:
                flow._building_choices.pop(CONF_HOUSE, None)
                return await flow.async_step_account_building_house()
            provider = create_provider(entry.data[CONF_PROVIDER], hass=flow.hass)
            try:
                address = await verify_building(provider, flow._building_values)
                if address is None:
                    errors["base"] = "invalid_building"
                else:
                    building = await ensure_building(
                        flow.hass, address, lambda: active(flow, entry)
                    )
                    if not active(flow, entry):
                        return flow.async_abort(reason="building_context_changed")
                    if building is None:
                        errors["base"] = "invalid_building"
                    else:
                        # Group selection belongs to this account; the public
                        # building keeps all groups and has no apartment fields.
                        return await flow.async_step_account_schedule_address(
                            {"schedule_address": building.entry_id}
                        )
            except ProviderError:
                errors["base"] = "cannot_connect"
            finally:
                await provider.close()
    default = unique_choice(
        flow._building_values.get(key, flow._building_suggestion.get(key)), choices, key
    )
    schema = vol.Schema(
        {
            vol.Required(
                key, **({"default": default} if default else {})
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(choices), mode=SelectSelectorMode.DROPDOWN
                )
            )
        }
    )
    if not choices:
        schema = vol.Schema({})
    return flow.async_show_form(
        step_id={
            CONF_CITY: "account_building_city",
            CONF_STREET: "account_building_street",
            CONF_HOUSE: "account_building_house",
        }[key],
        data_schema=schema,
        errors=errors,
    )
