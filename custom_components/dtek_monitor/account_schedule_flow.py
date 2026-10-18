"""Private account/building selection with explicit, user-confirmed group choices."""

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .account import CONF_ACCOUNTS
from .account_schedule import CONF_ACCOUNT_SCHEDULES, address_entries


def selector(choices, multiple=False):
    return SelectSelector(
        SelectSelectorConfig(
            options=choices, multiple=multiple, mode=SelectSelectorMode.DROPDOWN
        )
    )


def save(flow, entry, link):
    links = dict(entry.options.get(CONF_ACCOUNT_SCHEDULES, {}))
    number = flow._schedule_account
    if number not in entry.data[CONF_ACCOUNTS]:
        return flow.async_abort(reason="no_accounts")
    if link is None:
        links.pop(number, None)
    else:
        links[number] = link
    return flow.async_create_entry(
        title="", data={**entry.options, CONF_ACCOUNT_SCHEDULES: links}
    )


async def schedule_account(flow, entry, user_input):
    accounts = entry.data[CONF_ACCOUNTS]
    errors = {}
    if user_input is not None:
        number = user_input.get("schedule_account")
        if isinstance(number, str) and number in accounts:
            flow._schedule_account = number
            return await flow.async_step_account_schedule_address()
        errors["base"] = "select_accounts"
    return flow.async_show_form(
        step_id="account_schedule",
        errors=errors,
        data_schema=vol.Schema(
            {
                vol.Required("schedule_account"): selector(
                    [
                        {"value": number, "label": f"••••{number[-4:]} — {address}"}
                        for number, address in accounts.items()
                    ]
                )
            }
        ),
    )


async def schedule_address(flow, entry, user_input):
    addresses = address_entries(flow.hass, entry)
    errors = {}
    if user_input is not None:
        address_id = user_input.get("schedule_address")
        if address_id == "none":
            return save(flow, entry, None)
        if isinstance(address_id, str) and address_id in addresses:
            flow._schedule_address = address_id
            flow._schedule_group_choices = None
            old = entry.options.get(CONF_ACCOUNT_SCHEDULES, {}).get(
                flow._schedule_account
            )
            address = addresses[address_id]
            coordinator = getattr(address, "runtime_data", None)
            if (
                old is None
                and address.state == ConfigEntryState.LOADED
                and coordinator
                and coordinator.address_success
                and coordinator.address_data.get("group_labels_visible")
                and len(coordinator.address_data.get("schedule_groups", [])) == 1
            ):
                return save(
                    flow,
                    entry,
                    {
                        "address_entry_id": address_id,
                        "groups": [],
                        "selection_source": "automatic",
                    },
                )
            return await flow.async_step_account_schedule_groups()
        errors["base"] = "invalid_schedule_address"
    old = entry.options.get(CONF_ACCOUNT_SCHEDULES, {}).get(flow._schedule_account, {})
    default = old.get("address_entry_id")
    return flow.async_show_form(
        step_id="account_schedule_address",
        errors=errors,
        data_schema=vol.Schema(
            {
                vol.Required(
                    "schedule_address",
                    default=default if default in addresses else "none",
                ): selector(
                    [
                        {"value": "none", "label": "—"},
                        *(
                            {"value": address.entry_id, "label": address.title}
                            for address in addresses.values()
                        ),
                    ]
                )
            }
        ),
    )


async def schedule_groups(flow, entry, user_input):
    address = address_entries(flow.hass, entry).get(flow._schedule_address)
    coordinator = (
        getattr(address, "runtime_data", None)
        if address and address.state == ConfigEntryState.LOADED
        else None
    )
    data = (
        coordinator.address_data if coordinator and coordinator.address_success else {}
    )
    current = (
        data.get("schedule_groups", []) if data.get("group_labels_visible") else []
    )
    # The list shown in a form never changes under the user's cursor. Recheck
    # submitted selections against fresh applicability instead of replacing them.
    if flow._schedule_group_choices is None:
        flow._schedule_group_choices = list(current)
    choices = flow._schedule_group_choices
    old = entry.options.get(CONF_ACCOUNT_SCHEDULES, {}).get(flow._schedule_account, {})
    default = (
        [g for g in old.get("groups", []) if g in choices]
        if old.get("address_entry_id") == flow._schedule_address
        else []
    )
    errors = {}
    if user_input is not None:
        groups = user_input.get("schedule_groups", [])
        if not isinstance(groups, list) or any(
            not isinstance(g, str) or g not in choices or g not in current
            for g in groups
        ):
            errors["base"] = "invalid_schedule_groups"
        elif address is None:
            errors["base"] = "invalid_schedule_address"
        else:
            return save(
                flow,
                entry,
                {
                    "address_entry_id": address.entry_id,
                    "groups": sorted(set(groups)),
                    "selection_source": "user" if groups else "automatic",
                },
            )
        if isinstance(groups, list):
            default = [g for g in groups if isinstance(g, str) and g in choices]
    elif not current:
        errors["base"] = "schedule_groups_unavailable"
    return flow.async_show_form(
        step_id="account_schedule_groups",
        errors=errors,
        description_placeholders={"building": address.title if address else "—"},
        data_schema=vol.Schema(
            {
                vol.Optional("schedule_groups", default=default): selector(
                    [{"value": group, "label": group} for group in choices],
                    multiple=True,
                )
            }
        ),
    )
