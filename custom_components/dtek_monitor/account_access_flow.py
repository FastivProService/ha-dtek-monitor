"""Native, explicit SMS steps for the operator's registration and recovery."""

from __future__ import annotations

import asyncio

import voluptuous as vol
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .account import ACCOUNT_PROVIDERS, AccountError, normalize_phone
from .account_access import AccessError, AccountAccessClient
from .account_client import AUTH_ORIGIN

ACCESS_IDLE_TIMEOUT = 15 * 60


def _text(kind=TextSelectorType.TEXT):
    return TextSelector(TextSelectorConfig(type=kind))


class AccountAccessFlowMixin:
    """Challenge IDs and HTTP sessions live only inside an unfinished flow."""

    _access_client = None
    _access_timer = None
    _access_completed = ""
    _login_phone = ""

    def _detach_access(self):
        if self._access_timer:
            self._access_timer.cancel()
            self._access_timer = None
        client, self._access_client = self._access_client, None
        return client

    def _expire_access(self):
        if client := self._detach_access():
            self.hass.async_create_task(client.close())

    def _touch_access(self):
        if self._access_timer:
            self._access_timer.cancel()
        self._access_timer = asyncio.get_running_loop().call_later(
            ACCESS_IDLE_TIMEOUT, self._expire_access
        )

    async def _close_access(self):
        if client := self._detach_access():
            await client.close()

    def async_remove(self):
        self._expire_access()
        self._account_credentials = {}
        self._login_phone = ""
        super().async_remove()

    async def async_step_account_access(self, user_input=None):
        if self._provider_id not in ACCOUNT_PROVIDERS:
            return self.async_abort(reason="account_not_supported")
        await self._close_access()
        options = ["account_login", "account_recover"]
        if self.context.get("source") != "reauth":
            options.insert(1, "account_register")
        return self.async_show_menu(step_id="account_access", menu_options=options)

    async def async_step_account_register(self, user_input=None):
        if self.context.get("source") == "reauth":
            return self.async_abort(reason="account_mismatch")
        return await self._access_start("registration", "account_register", user_input)

    async def async_step_account_recover(self, user_input=None):
        return await self._access_start(
            "password-recovery", "account_recover", user_input
        )

    async def _access_start(self, kind, step_id, user_input):
        if self._provider_id not in ACCOUNT_PROVIDERS:
            return self.async_abort(reason="account_not_supported")
        errors = {}
        reauth = (
            self._get_reauth_entry() if self.context.get("source") == "reauth" else None
        )
        phone = reauth.data[CONF_USERNAME] if reauth else self._login_phone
        if user_input is not None:
            phone = user_input.get(CONF_USERNAME, "")
            try:
                phone = normalize_phone(phone)
                if reauth and phone != normalize_phone(reauth.data[CONF_USERNAME]):
                    raise AccessError("access_phone_mismatch")
                if not self._access_client:
                    self._access_client = AccountAccessClient()
                self._touch_access()
                await self._access_client.start(kind, phone)
                self._login_phone = phone
                return await self.async_step_account_sms()
            except ValueError:
                errors[CONF_USERNAME] = "invalid_phone"
            except AccessError as err:
                errors["base"] = err.code
            except AccountError:
                errors["base"] = "access_cannot_connect"
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=phone): _text(
                        TextSelectorType.TEL
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "seconds": str(
                    self._access_client.retry_seconds if self._access_client else 0
                )
            },
        )

    async def _access_error(self, err):
        if err.code in ("access_expired", "access_finalize_uncertain"):
            await self._close_access()
            return self.async_abort(reason=err.code)
        return None

    async def async_step_account_sms(self, user_input=None):
        client = self._access_client
        if not client:
            return self.async_abort(reason="access_expired")
        self._touch_access()
        errors = {}
        if user_input is not None:
            try:
                if user_input.get("resend") is True:
                    await client.start(client.kind, client.phone)
                else:
                    await client.verify(user_input.get("verification_code", "").strip())
                    if client.phase == "profile":
                        return await self.async_step_account_profile()
                    return await self.async_step_account_recovery_password()
            except AccessError as err:
                if result := await self._access_error(err):
                    return result
                errors["base"] = err.code
            except AccountError:
                errors["base"] = "access_cannot_connect"
        return self.async_show_form(
            step_id="account_sms",
            data_schema=vol.Schema(
                {
                    vol.Optional("verification_code"): _text(),
                    vol.Optional("resend", default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders={
                "phone": "••••" + client.phone[-4:],
                "seconds": str(client.retry_seconds),
            },
        )

    async def async_step_account_profile(self, user_input=None):
        client = self._access_client
        if not client or client.phase != "profile":
            return self.async_abort(reason="access_expired")
        self._touch_access()
        errors = {}
        if user_input is not None:
            try:
                await client.profile(
                    user_input.get("first_name", ""),
                    user_input.get("last_name", ""),
                    user_input.get("middle_name", ""),
                )
                return await self.async_step_account_registration_password()
            except AccessError as err:
                if result := await self._access_error(err):
                    return result
                errors["base"] = err.code
        return self.async_show_form(
            step_id="account_profile",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "last_name", default=(user_input or {}).get("last_name", "")
                    ): _text(),
                    vol.Required(
                        "first_name", default=(user_input or {}).get("first_name", "")
                    ): _text(),
                    vol.Optional(
                        "middle_name", default=(user_input or {}).get("middle_name", "")
                    ): _text(),
                }
            ),
            errors=errors,
        )

    async def async_step_account_registration_password(self, user_input=None):
        return await self._access_password(
            "registration", "account_registration_password", user_input
        )

    async def async_step_account_recovery_password(self, user_input=None):
        return await self._access_password(
            "password-recovery", "account_recovery_password", user_input
        )

    async def _access_password(self, kind, step_id, user_input):
        client = self._access_client
        if not client or client.phase != "password" or client.kind != kind:
            return self.async_abort(reason="access_expired")
        self._touch_access()
        errors = {}
        consent_field = "accept_terms" if kind == "registration" else "confirm_change"
        if user_input is not None:
            try:
                if user_input.get(consent_field) is not True:
                    raise AccessError(
                        "access_consent_required"
                        if kind == "registration"
                        else "access_confirm_change"
                    )
                password = user_input.get(CONF_PASSWORD, "")
                await client.finalize(
                    password,
                    user_input.get("confirm_password", ""),
                    consent=user_input[consent_field],
                )
                phone = client.phone
                await self._close_access()
                self._access_completed = kind
                self._login_phone = phone
                return await self.async_step_account_login(
                    {CONF_USERNAME: phone, CONF_PASSWORD: password}
                )
            except AccessError as err:
                if result := await self._access_error(err):
                    return result
                errors["base"] = err.code
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): _text(TextSelectorType.PASSWORD),
                    vol.Required("confirm_password"): _text(TextSelectorType.PASSWORD),
                    vol.Required(consent_field, default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders={"signup_url": AUTH_ORIGIN + "/signup"},
        )
