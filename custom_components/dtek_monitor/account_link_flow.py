"""Review cabinet changes separately from local monitoring and dashboard choices."""

from __future__ import annotations

import asyncio
from time import monotonic
from weakref import WeakValueDictionary

import voluptuous as vol
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .account import (
    CONF_ACCOUNTS,
    CONF_ACCOUNT_SUBJECT,
    CONF_ENTRY_KIND,
    ENTRY_ACCOUNT,
    AccountAuthError,
    AccountError,
    AccountRateLimitError,
    account_key,
)
from .account_client import create_account_provider
from .account import AccountAPIChanged
from .account_api_health import report_api_issue, clear_api_issue, watch_api
from .account_links import AccountLinkError, household_number
from .account_providers import (
    account_connection_title,
    account_provider_supported,
    get_account_provider_spec,
)
from .const import DOMAIN
from .providers import CONF_PROVIDER

REVIEW_SECONDS = 300


class AccountLinkFlowMixin:
    """Native HA admin-only setup/options forms; no account changes on opening."""

    _link_identity = None
    _link_review = None
    _unlink_choices = None

    def async_remove(self):
        self._link_identity = self._link_review = self._unlink_choices = None
        super().async_remove()

    def _link_context(self):
        entry = getattr(self, "_config_entry", None)
        data = entry.data if entry else getattr(self, "_account_credentials", {})
        provider = (
            data.get(CONF_PROVIDER) if entry else getattr(self, "_provider_id", None)
        )
        if not account_provider_supported(provider) or not data.get(
            CONF_ACCOUNT_SUBJECT
        ):
            raise AccountLinkError("link_session_changed")
        if entry and (
            entry.data.get(CONF_ENTRY_KIND) != ENTRY_ACCOUNT
            or self.hass.config_entries.async_get_entry(entry.entry_id) is not entry
        ):
            raise AccountLinkError("link_session_changed")
        identity = (
            provider,
            data.get("username"),
            data.get("password"),
            data[CONF_ACCOUNT_SUBJECT],
        )
        if self._link_identity is not None and identity != self._link_identity:
            raise AccountLinkError("link_session_changed")
        return identity

    def _assert_link_context(self, expected):
        if self._link_context() != expected:
            raise AccountLinkError("link_session_changed")

    def _assert_unlink_safe(self, number):
        # Registry objects remain until unload has drained in-flight submissions.
        provider = self._link_context()[0]
        prefix = f"account:{account_key(provider, number)}:"
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if (
                entry.data.get(CONF_PROVIDER) == provider
                and entry.data.get(CONF_ENTRY_KIND) == ENTRY_ACCOUNT
                and number in entry.data.get(CONF_ACCOUNTS, {})
            ):
                raise AccountLinkError("link_in_use")
        if any(
            e.platform == DOMAIN and e.unique_id.startswith(prefix)
            for e in er.async_get(self.hass).entities.values()
        ):
            raise AccountLinkError("link_in_use")

    async def _with_link_client(self, operation):
        identity = self._link_context()
        self._link_identity = identity
        locks = self.hass.data.setdefault(
            "dtek_monitor_account_link_locks", WeakValueDictionary()
        )
        key = account_key(identity[0], identity[3])
        lock = locks.setdefault(key, asyncio.Lock())
        async with lock:
            self._assert_link_context(identity)
            client = create_account_provider(identity[0])
            watch_api(self.hass, identity[0], client)
            try:
                if await client.authenticate(identity[1], identity[2]) != identity[3]:
                    raise AccountLinkError("link_session_changed")
                self._assert_link_context(identity)
                result = await operation(client)
                self._assert_link_context(identity)
                return result
            finally:
                await client.close()

    def _link_error(self, error):
        if isinstance(error, AccountAPIChanged):
            report_api_issue(self.hass, self._link_identity[0], error)
            return "account_api_changed"
        if isinstance(error, AccountLinkError):
            return error.code
        if isinstance(error, AccountAuthError):
            return "account_auth_required"
        if isinstance(error, AccountRateLimitError):
            return "link_rate_limit"
        return "account_cannot_connect"

    async def async_step_account_setup(self, user_input=None):
        options = (
            ["accounts", "account_link"]
            if getattr(self, "_personal_accounts", {})
            else ["account_link", "account_finish_empty"]
        )
        return self.async_show_menu(step_id="account_setup", menu_options=options)

    async def async_step_account_finish_empty(self, user_input=None):
        if not getattr(self, "_account_credentials", None):
            return self.async_abort(reason="link_session_changed")
        credentials, self._account_credentials = self._account_credentials, {}
        return self.async_create_entry(
            title=account_connection_title(self._provider_id, credentials["username"]),
            description="account_connected_empty",
            data={
                CONF_ENTRY_KIND: ENTRY_ACCOUNT,
                CONF_PROVIDER: self._provider_id,
                **credentials,
                CONF_ACCOUNTS: {},
            },
        )

    async def async_step_account_management(self, user_input=None):
        return self.async_show_menu(
            step_id="account_management",
            menu_options=["account_link", "account_selection", "account_unlink"],
        )

    async def async_step_account_link(self, user_input=None):
        errors = {}
        try:
            provider = self._link_context()[0]
            spec = get_account_provider_spec(provider)
        except AccountError as err:
            return self.async_abort(reason=self._link_error(err))
        if user_input is not None:
            self._link_review = None
            try:
                number = household_number(user_input.get("account_number", ""), spec)

                async def lookup(client):
                    if number in {a.number for a in await client.list_accounts()}:
                        raise AccountLinkError("link_already_linked")
                    return await client.lookup_account(number)

                candidate = await self._with_link_client(lookup)
                clear_api_issue(self.hass, provider, "link_lookup")
                self._link_review = ("link", candidate, monotonic())
                return await self.async_step_account_link_confirm()
            except AccountError as err:
                errors["base"] = self._link_error(err)
        return self.async_show_form(
            step_id="account_link",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "account_number",
                        default=(user_input or {}).get("account_number", ""),
                    ): TextSelector()
                }
            ),
            errors=errors,
            description_placeholders={
                "account_length": str(spec.household_length),
                "account_prefixes": ", ".join(spec.household_prefixes),
            },
        )

    async def async_step_account_unlink(self, user_input=None):
        errors = {}
        if not getattr(self, "_config_entry", None):
            return self.async_abort(reason="link_session_changed")
        try:
            if self._unlink_choices is None:

                async def linked(client):
                    return {a.number: a for a in await client.list_accounts()}

                self._unlink_choices = await self._with_link_client(linked)
            if not self._unlink_choices:
                return self.async_abort(reason="link_none")
            if user_input is not None:
                number = user_input.get("account_number")
                if not isinstance(number, str) or number not in self._unlink_choices:
                    raise AccountLinkError("link_not_linked")
                self._assert_unlink_safe(number)
                self._link_review = (
                    "unlink",
                    self._unlink_choices[number],
                    monotonic(),
                )
                return await self.async_step_account_unlink_confirm()
        except AccountError as err:
            if self._unlink_choices is None:
                return self.async_abort(reason=self._link_error(err))
            errors["base"] = self._link_error(err)
        return self.async_show_form(
            step_id="account_unlink",
            data_schema=vol.Schema(
                {
                    vol.Required("account_number"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": a.number, "label": a.label}
                                for a in (self._unlink_choices or {}).values()
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_account_link_confirm(self, user_input=None):
        return await self._confirm_link("link", user_input)

    async def async_step_account_unlink_confirm(self, user_input=None):
        return await self._confirm_link("unlink", user_input)

    async def _confirm_link(self, action, user_input):
        review = self._link_review
        if (
            not review
            or review[0] != action
            or monotonic() - review[2] > REVIEW_SECONDS
        ):
            self._link_review = None
            return self.async_abort(reason="link_review_expired")
        candidate = review[1]
        errors = {}
        if user_input is not None:
            if user_input.get("confirm") is not True:
                errors["base"] = "link_confirm_required"
            else:
                # Consume before awaiting: a repeated submit cannot replay a write.
                self._link_review = None
                try:

                    async def change(client):
                        def authorize():
                            self._assert_link_context(self._link_identity)
                            if monotonic() - review[2] > REVIEW_SECONDS:
                                raise AccountLinkError("link_review_expired")
                            if action == "unlink":
                                self._assert_unlink_safe(candidate.number)

                        authorize()
                        await (
                            client.link_account
                            if action == "link"
                            else client.unlink_account
                        )(candidate, authorize)

                    await self._with_link_client(change)
                    clear_api_issue(
                        self.hass,
                        self._link_identity[0],
                        "link_add" if action == "link" else "link_remove",
                    )
                except AccountError as err:
                    return self.async_abort(reason=self._link_error(err))
                if action == "unlink":
                    return self.async_abort(reason="link_unlinked")
                return await self.async_step_account_linked()
        return self.async_show_form(
            step_id="account_link_confirm"
            if action == "link"
            else "account_unlink_confirm",
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
            errors=errors,
            description_placeholders={
                "number": candidate.number,
                "address": candidate.address,
            },
        )

    async def async_step_account_linked(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:

                async def linked(client):
                    return {a.number: a for a in await client.list_accounts()}

                choices = await self._with_link_client(linked)
                if getattr(self, "_config_entry", None):
                    self._account_choices = choices
                    return await self.async_step_account_selection()
                self._personal_accounts = choices
                return await self.async_step_account_setup()
            except AccountError as err:
                errors["base"] = self._link_error(err)
        return self.async_show_form(
            step_id="account_linked", data_schema=vol.Schema({}), errors=errors
        )
