"""Optional cabinet setup and address discovery; no dependency on the bot."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .account import (
    ACCOUNT_PROVIDERS,
    CONF_ACCOUNTS,
    CONF_ACCOUNT_SUBJECT,
    CONF_ENTRY_KIND,
    ENTRY_ACCOUNT,
    AccountAuthError,
    AccountError,
    AccountRateLimitError,
    account_key,
    normalize_phone,
)
from .account_client import create_account_provider
from .const import DOMAIN
from .providers import CONF_PROVIDER, DEFAULT_PROVIDER_ID


class AccountFlowMixin:
    """Methods shared by the integration's config flow, not a separate domain."""

    async def async_step_connection(self, user_input=None):
        options = ["address"]
        if self._provider_id in ACCOUNT_PROVIDERS:
            options.append("account_login")
        return self.async_show_menu(step_id="connection", menu_options=options)

    async def async_step_address(self, user_input=None):
        from .providers import PROVIDERS

        spec = PROVIDERS[self._provider_id]
        if spec.fixed_city:
            self._city = spec.fixed_city
            return await self.async_step_street()
        return await self.async_step_city()

    async def async_step_account_login(self, user_input=None):
        if self._provider_id not in ACCOUNT_PROVIDERS:
            return self.async_abort(reason="account_not_supported")
        errors = {}
        reauth_entry = (
            self._get_reauth_entry() if self.context.get("source") == "reauth" else None
        )
        if user_input is not None:
            client = create_account_provider(self._provider_id)
            try:
                phone = normalize_phone(user_input[CONF_USERNAME])
                subject = await client.authenticate(phone, user_input[CONF_PASSWORD])
                if reauth_entry and subject != reauth_entry.data[CONF_ACCOUNT_SUBJECT]:
                    return self.async_abort(reason="account_mismatch")
                accounts = await client.list_accounts()
                if not accounts:
                    errors["base"] = "no_accounts"
                else:
                    if reauth_entry:
                        return self.async_update_reload_and_abort(
                            reauth_entry,
                            data_updates={
                                CONF_USERNAME: phone,
                                CONF_PASSWORD: user_input[CONF_PASSWORD],
                            },
                        )
                    await self.async_set_unique_id(
                        "account:" + account_key(self._provider_id, subject)
                    )
                    self._abort_if_unique_id_configured()
                    self._account_credentials = {
                        CONF_USERNAME: phone,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_ACCOUNT_SUBJECT: subject,
                    }
                    self._personal_accounts = {a.number: a for a in accounts}
                    return await self.async_step_accounts()
            except ValueError:
                errors[CONF_USERNAME] = "invalid_phone"
            except AccountAuthError:
                errors["base"] = "invalid_auth"
            except AccountRateLimitError:
                errors["base"] = "account_rate_limit"
            except AccountError:
                errors["base"] = "account_cannot_connect"
            finally:
                await client.close()
        return self.async_show_form(
            step_id="account_login",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=(
                            reauth_entry.data[CONF_USERNAME] if reauth_entry else ""
                        ),
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEL)),
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_accounts(self, user_input=None):
        available = getattr(self, "_personal_accounts", {})
        if not available:
            return await self.async_step_account_login()
        errors = {}
        if user_input is not None:
            selected = user_input.get(CONF_ACCOUNTS)
            if (
                not isinstance(selected, list)
                or not selected
                or any(not isinstance(n, str) or n not in available for n in selected)
            ):
                errors["base"] = "select_accounts"
            else:
                claimed = {
                    number
                    for entry in self.hass.config_entries.async_entries(DOMAIN)
                    if entry.data.get(CONF_ENTRY_KIND) == ENTRY_ACCOUNT
                    and entry.data.get(CONF_PROVIDER) == self._provider_id
                    for number in entry.data.get(CONF_ACCOUNTS, {})
                }
                if claimed.intersection(selected):
                    result = await self.async_step_accounts()
                    result["errors"] = {"base": "accounts_already_configured"}
                    return result
                credentials = self._account_credentials
                self._account_credentials = {}
                return self.async_create_entry(
                    title=f"Personal account • ••••{credentials[CONF_USERNAME][-4:]}",
                    data={
                        CONF_ENTRY_KIND: ENTRY_ACCOUNT,
                        CONF_PROVIDER: self._provider_id,
                        **credentials,
                        CONF_ACCOUNTS: {n: available[n].address for n in selected},
                    },
                )
        return self.async_show_form(
            step_id="accounts",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCOUNTS): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": a.number, "label": a.label}
                                for a in available.values()
                            ],
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data):
        if entry_data.get(CONF_ENTRY_KIND) != ENTRY_ACCOUNT:
            return self.async_abort(reason="account_not_supported")
        self._provider_id = entry_data.get(CONF_PROVIDER, DEFAULT_PROVIDER_ID)
        return await self.async_step_account_login()

    async def async_step_integration_discovery(self, discovery_info):
        """Independent address flow: user verifies every public-directory field."""
        self._provider_id = discovery_info[CONF_PROVIDER]
        self.context["title_placeholders"] = {"name": "DTEK address"}
        self._address_suggestion = discovery_info.get("suggestion", {})
        return await self.async_step_address()


def building_suggestion(address: str) -> dict[str, str]:
    """Conservative prefill only; never use apartment/account IDs as addresses.

    A match is not a confirmed address: the ordinary public-directory forms
    still require the user's selection. Ambiguous formats get no prefill.
    """
    match = re.fullmatch(
        r"\s*(?P<city>[^,]+),\s*(?P<street>.+?)\s+буд\.\s*"
        r"(?P<house_num>[\w/\-]+)(?:,\s*(?:кв\.?|к\.|квартира)\s*[\w/\-]+)?\s*",
        address,
    )
    return {k: v.strip() for k, v in match.groupdict().items()} if match else {}


async def account_options(flow, entry, user_input: dict[str, Any] | None):
    """Offer public address setup without passing cabinet credentials onward."""
    accounts = entry.data[CONF_ACCOUNTS]
    errors = {}
    if user_input is not None:
        number = user_input.get("address_account")
        if not isinstance(number, str) or number not in accounts:
            errors["base"] = "select_accounts"
        else:
            await flow.hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_INTEGRATION_DISCOVERY},
                data={
                    CONF_PROVIDER: entry.data[CONF_PROVIDER],
                    "suggestion": building_suggestion(accounts[number]),
                },
            )
            return flow.async_abort(reason="address_setup_started")
    return flow.async_show_form(
        step_id="account",
        data_schema=vol.Schema(
            {
                vol.Required("address_account"): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": n, "label": f"••••{n[-4:]} — {address}"}
                            for n, address in accounts.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        ),
        errors=errors,
    )
