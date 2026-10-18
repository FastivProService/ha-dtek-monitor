"""Config flow for DTEK Monitor integration."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_CITY,
    CONF_HOUSE,
    CONF_SCAN_INTERVAL,
    CONF_STREET,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
)
from .provider import Address, ProviderError
from .providers import CONF_PROVIDER, DEFAULT_PROVIDER_ID, PROVIDERS, create_provider
from .account import CONF_ENTRY_KIND, ENTRY_ACCOUNT
from .account_flow import AccountFlowMixin, account_options, select_account_options
from .account_link_flow import AccountLinkFlowMixin

_LOGGER = logging.getLogger(__name__)


def _combo_select(options: list[str]) -> SelectSelector:
    """Create a combo-box selector: dropdown + type-to-filter.

    custom_value=True makes it a real combobox where the user can type,
    and the dropdown filters matching options as they type.
    """
    return SelectSelector(
        SelectSelectorConfig(
            options=[SelectOptionDict(value=o, label=o) for o in options],
            mode=SelectSelectorMode.DROPDOWN,
            custom_value=True,
            sort=False,
        )
    )


class DTEKMonitorConfigFlow(AccountFlowMixin, ConfigFlow, domain=DOMAIN):
    """Handle a config flow for DTEK Monitor."""

    VERSION = 4

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow for this handler."""
        return DTEKMonitorOptionsFlow(config_entry)

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._provider_id = DEFAULT_PROVIDER_ID
        self._streets_data: dict[str, list[str]] = {}
        self._city: str = ""
        self._street: str = ""
        self._house: str = ""
        self._available_houses: list[str] = []
        self._address_suggestion: dict[str, str] = {}

    async def _fetch_streets(self) -> dict[str, list[str]]:
        """Cache options, closing owned HTTP sessions even if the flow aborts."""
        if not self._streets_data:
            provider = create_provider(self._provider_id)
            try:
                self._streets_data = {
                    city: list(await provider.list_streets(city))
                    for city in await provider.list_localities()
                }
            finally:
                await provider.close()
        return self._streets_data

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose an enabled operator before requesting its address directory."""
        errors = {}
        if user_input is not None:
            provider_id = user_input.get(CONF_PROVIDER)
            spec = PROVIDERS.get(provider_id) if isinstance(provider_id, str) else None
            if spec is None or not spec.enabled:
                errors[CONF_PROVIDER] = "invalid_provider"
            else:
                if provider_id != self._provider_id:
                    self._streets_data = {}
                    self._city = self._street = self._house = ""
                    self._available_houses = []
                self._provider_id = provider_id
                return await self.async_step_connection()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PROVIDER, default=self._provider_id
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=p.id, label=p.name)
                                for p in PROVIDERS.values()
                                if p.enabled
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                            custom_value=False,
                            sort=False,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_city(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a locality in the chosen operator's directory."""
        errors: dict[str, str] = {}

        try:
            streets_data = await self._fetch_streets()
            cities = sorted(streets_data)
            if user_input is not None and CONF_CITY in user_input:
                city = str(user_input[CONF_CITY]).strip()
                if city not in streets_data:
                    errors[CONF_CITY] = "invalid_city"
                else:
                    self._city = city
                    return await self.async_step_street()
        except ProviderError as err:
            _LOGGER.error("Failed to fetch cities from DTEK: %s", err)
            errors["base"] = "cannot_connect"
            cities = []

        if not cities and not errors:
            errors["base"] = "no_data"

        if not cities:
            return self.async_show_form(
                step_id="city",
                data_schema=vol.Schema({}),
                errors=errors,
            )

        return self.async_show_form(
            step_id="city",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CITY, **self._suggested_default(CONF_CITY, cities)
                    ): _combo_select(cities),
                }
            ),
            errors=errors,
        )

    async def async_step_street(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Select street."""
        errors: dict[str, str] = {}

        try:
            streets_data = await self._fetch_streets()
        except ProviderError:
            return self.async_show_form(
                step_id="street",
                data_schema=vol.Schema({}),
                errors={"base": "cannot_connect"},
            )
        valid_streets = streets_data.get(self._city, [])

        if user_input is not None and CONF_STREET in user_input:
            street = str(user_input[CONF_STREET]).strip()
            if street not in valid_streets:
                errors[CONF_STREET] = "invalid_street"
            else:
                self._street = street
                return await self.async_step_house()

        streets = sorted(valid_streets)

        if not streets:
            errors["base"] = "no_streets"
            return self.async_show_form(
                step_id="street",
                data_schema=vol.Schema({}),
                errors=errors,
            )

        return self.async_show_form(
            step_id="street",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_STREET, **self._suggested_default(CONF_STREET, streets)
                    ): _combo_select(streets),
                }
            ),
            errors=errors,
            description_placeholders={"city": self._city},
        )

    async def async_step_house(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: Select house number."""
        errors: dict[str, str] = {}

        if user_input is not None:
            house = str(user_input[CONF_HOUSE]).strip()
            if self.context.get("source") == "integration_discovery" and (
                not self._available_houses or house not in self._available_houses
            ):
                result = await self.async_step_house()
                result["errors"] = {"house_num": "invalid_house"}
                return result
            self._house = house

            # Keep the legacy OEM identifier so duplicate detection and existing
            # registries continue to work. New operators use unambiguous IDs.
            unique_id = (
                f"{self._city}_{self._street}_{self._house}"
                if self._provider_id == DEFAULT_PROVIDER_ID
                else Address(
                    self._provider_id, self._city, self._street, self._house
                ).identity
            )
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            return await self.async_step_settings()

        try:
            provider = create_provider(self._provider_id)
            try:
                houses = await provider.list_houses(self._city, self._street)
            finally:
                await provider.close()
            self._available_houses = sorted(houses, key=_natural_sort_key)
        except ProviderError as err:
            _LOGGER.error("Failed to fetch houses from DTEK: %s", err)
            errors["base"] = "cannot_connect"
            self._available_houses = []

        if not self._available_houses:
            if not errors:
                errors["base"] = "no_houses"
            return self.async_show_form(
                step_id="house",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_HOUSE): str,
                    }
                ),
                errors=errors,
                description_placeholders={
                    "city": self._city,
                    "street": self._street,
                },
            )

        return self.async_show_form(
            step_id="house",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOUSE,
                        **self._suggested_default(CONF_HOUSE, self._available_houses),
                    ): _combo_select(self._available_houses),
                }
            ),
            errors=errors,
            description_placeholders={
                "city": self._city,
                "street": self._street,
            },
        )

    def _suggested_default(self, key: str, choices: list[str]) -> dict:
        value = self._address_suggestion.get(key)
        if value in choices:
            return {"default": value}
        if not value:
            return {}

        def normalize(text):
            text = " ".join(text.casefold().split())
            if key == CONF_CITY:
                text = re.sub(r"^(?:м\.|місто)\s+", "", text)
            elif key == CONF_STREET:
                text = re.sub(r"^вул\.\s+", "вулиця ", text)
            return text

        matches = [
            choice for choice in choices if normalize(choice) == normalize(value)
        ]
        return {"default": matches[0]} if len(matches) == 1 else {}

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 4: Configure settings (scan interval)."""
        if user_input is not None:
            scan_interval = int(user_input[CONF_SCAN_INTERVAL])

            return self.async_create_entry(
                title=(
                    f"{self._street}, {self._house}"
                    if self._provider_id == DEFAULT_PROVIDER_ID
                    else f"{self._city} • {self._street}, {self._house} ({PROVIDERS[self._provider_id].name})"
                ),
                data={
                    CONF_ENTRY_KIND: "address",
                    CONF_PROVIDER: self._provider_id,
                    CONF_CITY: self._city,
                    CONF_STREET: self._street,
                    CONF_HOUSE: self._house,
                    CONF_SCAN_INTERVAL: scan_interval,
                },
            )

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=DEFAULT_SCAN_INTERVAL_SECONDS,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL_SECONDS,
                            max=MAX_SCAN_INTERVAL_SECONDS,
                            step=30,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.SLIDER,
                        )
                    ),
                }
            ),
            description_placeholders={
                "city": self._city,
                "street": self._street,
                "house": self._house,
            },
        )


def _natural_sort_key(value: str) -> tuple:
    """Sort house numbers naturally: 1, 2, 10, 10A, 56V."""
    parts = re.split(r"(\d+)", value)
    result = []
    for part in parts:
        if part.isdigit():
            result.append((0, int(part), ""))
        else:
            result.append((1, 0, part.lower()))
    return tuple(result)


class DTEKMonitorOptionsFlow(AccountLinkFlowMixin, OptionsFlow):
    """Handle DTEK Monitor options."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage runtime options for an existing entry."""
        if self._config_entry.data.get(CONF_ENTRY_KIND) == ENTRY_ACCOUNT:
            return await self.async_step_account_menu()
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                },
            )

        current_interval = int(
            self._config_entry.options.get(
                CONF_SCAN_INTERVAL,
                self._config_entry.data.get(
                    CONF_SCAN_INTERVAL,
                    DEFAULT_SCAN_INTERVAL_SECONDS,
                ),
            )
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=current_interval,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL_SECONDS,
                            max=MAX_SCAN_INTERVAL_SECONDS,
                            step=30,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.SLIDER,
                        )
                    ),
                }
            ),
        )

    async def async_step_account(self, user_input=None):
        return await account_options(self, self._config_entry, user_input)

    async def async_step_account_menu(self, user_input=None):
        if not self._config_entry.data.get("accounts"):
            return await self.async_step_account_management()
        return self.async_show_menu(
            step_id="account_menu",
            menu_options=[
                "account_management",
                "account_selection",
                "account",
                "account_schedule",
                "account_reminders",
                "account_checks",
                "account_notices",
            ],
        )

    async def async_step_account_reminders(self, user_input=None):
        from .account_reminder_flow import choose_account

        return await choose_account(self, self._config_entry, user_input)

    async def async_step_account_notices(self, user_input=None):
        from .account_notification_flow import choose_account

        return await choose_account(self, self._config_entry, user_input)

    async def async_step_account_notice_settings(self, user_input=None):
        from .account_notification_flow import configure

        return await configure(self, self._config_entry, user_input)

    async def async_step_account_checks(self, user_input=None):
        from .account_check_flow import choose_account

        return await choose_account(self, self._config_entry, user_input)

    async def async_step_account_check_settings(self, user_input=None):
        from .account_check_flow import configure

        return await configure(self, self._config_entry, user_input)

    async def async_step_account_reminder_settings(self, user_input=None):
        from .account_reminder_flow import configure

        return await configure(self, self._config_entry, user_input)

    async def async_step_account_schedule(self, user_input=None):
        from .account_schedule_flow import schedule_account

        return await schedule_account(self, self._config_entry, user_input)

    async def async_step_account_schedule_address(self, user_input=None):
        from .account_schedule_flow import schedule_address

        return await schedule_address(self, self._config_entry, user_input)

    async def async_step_account_schedule_groups(self, user_input=None):
        from .account_schedule_flow import schedule_groups

        return await schedule_groups(self, self._config_entry, user_input)

    async def async_step_account_selection(self, user_input=None):
        return await select_account_options(self, self._config_entry, user_input)
