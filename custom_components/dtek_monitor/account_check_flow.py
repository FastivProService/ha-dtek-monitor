"""Opt-in per-account dry-run schedule. No billing enable switch exists here."""

from datetime import time
import voluptuous as vol
from homeassistant.helpers.selector import (
    BooleanSelector,
    TimeSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .account import CONF_ACCOUNTS
from .account_checks import CONF_CHECKS, settings
from .account_schedule_flow import selector


async def choose_account(flow, entry, user_input):
    errors = {}
    accounts = entry.data[CONF_ACCOUNTS]
    if user_input is not None:
        number = user_input.get("check_account")
        if isinstance(number, str) and number in accounts:
            flow._check_account = number
            return await flow.async_step_account_check_settings()
        errors["base"] = "select_accounts"
    return flow.async_show_form(
        step_id="account_checks",
        errors=errors,
        data_schema=vol.Schema(
            {
                vol.Required("check_account"): selector(
                    [
                        {"value": n, "label": f"••••{n[-4:]} — {address}"}
                        for n, address in accounts.items()
                    ]
                )
            }
        ),
    )


async def configure(flow, entry, user_input):
    number = flow._check_account
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
                or not isinstance(user_input.get("enabled"), bool)
                or user_input.get("frequency") not in ("daily", "reading_window")
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            errors["base"] = "invalid_check_settings"
            defaults.update({k: v for k, v in user_input.items() if k in defaults})
        else:
            checks = dict(entry.options.get(CONF_CHECKS, {}))
            checks[number] = {
                "enabled": user_input["enabled"],
                "time": clock.isoformat(),
                "frequency": user_input["frequency"],
            }
            return flow.async_create_entry(
                title="", data={**entry.options, CONF_CHECKS: checks}
            )
    return flow.async_show_form(
        step_id="account_check_settings",
        errors=errors,
        description_placeholders={"account": f"••••{number[-4:]}"},
        data_schema=vol.Schema(
            {
                vol.Required("enabled", default=defaults["enabled"]): BooleanSelector(),
                vol.Required("time", default=defaults["time"]): TimeSelector(),
                vol.Required(
                    "frequency", default=defaults["frequency"]
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=["daily", "reading_window"],
                        translation_key="check_frequency",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        ),
    )
