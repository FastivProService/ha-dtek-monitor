"""Independent personal-account refresh lifecycle; no public-address dependency."""

from datetime import timedelta
import logging

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .account import (
    ACCOUNT_UPDATE_HOURS,
    CONF_ACCOUNTS,
    CONF_ACCOUNT_SUBJECT,
    AccountAuthError,
    AccountError,
    AccountRateLimitError,
)

_LOGGER = logging.getLogger(__name__)


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
        self.entry = entry
        self._authenticated = False

    async def _async_update_data(self):
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
