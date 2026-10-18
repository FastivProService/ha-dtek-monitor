"""Per-account reminder preferences; no mutation before submitting the form."""

from datetime import time

import voluptuous as vol
from homeassistant.helpers.selector import BooleanSelector, TimeSelector

from .account import CONF_ACCOUNTS
from .account_reminders import CONF_REMINDERS, settings
from .account_schedule_flow import selector


async def choose_account(flow, entry, user_input):
    errors = {}
    accounts = entry.data[CONF_ACCOUNTS]
    if user_input is not None:
        number = user_input.get("reminder_account")
        if isinstance(number, str) and number in accounts:
            flow._reminder_account = number
            return await flow.async_step_account_reminder_settings()
        errors["base"] = "select_accounts"
    return flow.async_show_form(
        step_id="account_reminders",
        errors=errors,
        data_schema=vol.Schema(
            {
                vol.Required("reminder_account"): selector(
                    [
                        {"value": n, "label": f"••••{n[-4:]} — {address}"}
                        for n, address in accounts.items()
                    ]
                )
            }
        ),
    )


async def configure(flow, entry, user_input):
    number = flow._reminder_account
    if number not in entry.data[CONF_ACCOUNTS]:
        return flow.async_abort(reason="no_accounts")
    defaults = settings(entry, number)
    errors = {}
    if user_input is not None:
        try:
            clock = time.fromisoformat(user_input["time"])
            if (
                clock.tzinfo
                or clock.microsecond
                or any(
                    not isinstance(user_input.get(key), bool)
                    for key in ("enabled", "include_estimate")
                )
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            errors["base"] = "invalid_reminder_settings"
            defaults.update({k: v for k, v in user_input.items() if k in defaults})
        else:
            reminders = dict(entry.options.get(CONF_REMINDERS, {}))
            reminders[number] = {
                "enabled": user_input["enabled"],
                "time": clock.isoformat(),
                "include_estimate": user_input["include_estimate"],
            }
            return flow.async_create_entry(
                title="", data={**entry.options, CONF_REMINDERS: reminders}
            )
    return flow.async_show_form(
        step_id="account_reminder_settings",
        errors=errors,
        data_schema=vol.Schema(
            {
                vol.Required("enabled", default=defaults["enabled"]): BooleanSelector(),
                vol.Required("time", default=defaults["time"]): TimeSelector(),
                vol.Required(
                    "include_estimate", default=defaults["include_estimate"]
                ): BooleanSelector(),
            }
        ),
    )
