"""Independent personal-account refresh lifecycle; no public-address dependency."""

from datetime import timedelta
import logging
import asyncio
import math
from time import monotonic

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .account import (
    ACCOUNT_UPDATE_HOURS,
    CONF_ACCOUNTS,
    CONF_ACCOUNT_SUBJECT,
    AccountAuthError,
    AccountError,
    AccountRateLimitError,
)
from .account_history import HistoryCache
from .account_energy_api import HAEnergyManager
from .account_submission import SubmissionManager
from .account_submission_store import SubmissionStore
from .providers import CONF_PROVIDER
from .account_reminders import AccountReminders

_LOGGER = logging.getLogger(__name__)
MANUAL_REFRESH_SECONDS = 60


class AccountCoordinator(DataUpdateCoordinator):
    """One authenticated session, with availability tracked per selected account."""

    def __init__(self, hass, entry, client):
        super().__init__(
            hass,
            _LOGGER,
            name="DTEK personal account",
            config_entry=entry,
            update_interval=timedelta(hours=ACCOUNT_UPDATE_HOURS),
        )
        self.client = client
        self.history = HistoryCache(client)
        self.energy = HAEnergyManager(hass, entry)
        self.submissions = SubmissionManager(
            client,
            SubmissionStore(
                hass.config.path(
                    ".storage", f"dtek_monitor.submissions.{entry.entry_id}"
                ),
                hass.async_add_executor_job,
            ),
            entry.data[CONF_PROVIDER],
            self.energy,
        )
        self.entry = entry
        self.reminders = AccountReminders(hass, entry, self)
        self._authenticated = False
        self._last_attempt = None
        self._manual_refresh_lock = asyncio.Lock()

    async def async_manual_refresh(self):
        """One limit per connection, including failed attempts and all buttons."""
        async with self._manual_refresh_lock:
            remaining = (
                MANUAL_REFRESH_SECONDS - (monotonic() - self._last_attempt)
                if self._last_attempt is not None
                else 0
            )
            if remaining > 0:
                raise ServiceValidationError(
                    translation_domain="dtek_monitor",
                    translation_key="account_refresh_cooldown",
                    translation_placeholders={"seconds": str(math.ceil(remaining))},
                )
            self._last_attempt = monotonic()
            await self.async_refresh()
            if not self.last_update_success or any(
                v is None for v in (self.data or {}).values()
            ):
                raise HomeAssistantError(
                    translation_domain="dtek_monitor",
                    translation_key="account_refresh_failed",
                )

    async def _async_update_data(self):
        self._last_attempt = monotonic()
        try:
            if not self._authenticated:
                subject = await self.client.authenticate(
                    self.entry.data[CONF_USERNAME], self.entry.data[CONF_PASSWORD]
                )
                if subject != self.entry.data[CONF_ACCOUNT_SUBJECT]:
                    raise AccountAuthError("Personal account identity changed")
                self._authenticated = True
            linked = {a.number for a in await self.client.list_accounts()}
            result = {}
            for number in self.entry.data[CONF_ACCOUNTS]:
                if number not in linked:
                    result[number] = None
                    continue
                try:
                    result[number] = await self.client.read_account(number)
                except (AccountAuthError, AccountRateLimitError):
                    raise
                except AccountError:
                    # One inaccessible object does not invalidate the others.
                    result[number] = None
            if not any(result.values()):
                raise UpdateFailed("No selected personal accounts could be updated")
            return result
        except AccountAuthError:
            raise ConfigEntryAuthFailed(
                "Personal account authentication required"
            ) from None
        except AccountError:
            raise UpdateFailed("Personal account service is unavailable") from None
