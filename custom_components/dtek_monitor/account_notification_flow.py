"""Per-account opt-in meter notifications."""

import voluptuous as vol
from homeassistant.helpers.selector import BooleanSelector

from .account import CONF_ACCOUNTS
from .account_notifications import CONF_NOTICES, settings
from .account_schedule_flow import selector


async def choose_account(flow, entry, user_input):
    accounts = entry.data[CONF_ACCOUNTS]
    if (
        user_input is not None
        and isinstance(user_input.get("notice_account"), str)
        and user_input["notice_account"] in accounts
    ):
        flow._notice_account = user_input["notice_account"]
        return await flow.async_step_account_notice_settings()
    return flow.async_show_form(
        step_id="account_notices",
        errors={"base": "select_accounts"} if user_input else {},
        data_schema=vol.Schema(
            {
                vol.Required("notice_account"): selector(
                    [
                        {"value": n, "label": f"••••{n[-4:]} — {address}"}
                        for n, address in accounts.items()
                    ]
                )
            }
        ),
    )


async def configure(flow, entry, user_input):
    number = flow._notice_account
    if number not in entry.data[CONF_ACCOUNTS]:
        return flow.async_abort(reason="no_accounts")
    defaults = settings(entry, number)
    errors = {}
    if user_input is not None:
        if all(type(user_input.get(key)) is bool for key in defaults):
            options = {
                **entry.options.get(CONF_NOTICES, {}),
                number: {k: user_input[k] for k in defaults},
            }
            return flow.async_create_entry(
                title="", data={**entry.options, CONF_NOTICES: options}
            )
        errors["base"] = "invalid_reminder_settings"
    return flow.async_show_form(
        step_id="account_notice_settings",
        errors=errors,
        data_schema=vol.Schema(
            {
                vol.Required(key, default=value): BooleanSelector()
                for key, value in defaults.items()
            }
        ),
    )
