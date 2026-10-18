# Personal account preview

**English** · [Українська](PERSONAL_ACCOUNT_PREVIEW.uk.md)

The `feature/personal-account` branch adds an optional, read-only DTEK personal
account connection. This is an experimental version (`1.8.0b1`), initially for
**DTEK Odesa Electric Networks household accounts only**. Address monitoring
continues to support all operators listed in the main README.

## Try the preview

Use a separate Home Assistant test instance. Copy `custom_components/dtek_monitor`
from this branch into its configuration directory and restart Home Assistant.
Do not install a second copy of the integration under another folder name.

This preview upgrades configuration entries to version 4. Before testing on an
existing installation, back up its configuration. Returning to version 1.7.0
requires restoring the earlier configuration backup as well as the earlier code.

1. Open **Settings → Devices & services → Add integration → DTEK Monitor**.
2. Select **ДТЕК Одеські електромережі**.
3. Select **Personal account**. For schedules without a login, select
   **Outage schedules by address** instead.
4. Enter the phone number and password used on the DTEK website.
5. Select the household accounts you want to monitor.
6. Open the devices created under the new personal account connection.

Home Assistant stores the login credentials in its configuration; protect your
configuration and backups. Authentication cookies and API tokens are held in
memory. If authentication expires, use Home Assistant's reauthentication flow
with the same DTEK account.

## Available data

- Recorded meter readings and their dates, separately for each returned tariff
  scale. These use DTEK's unblocked **previous** readings, not pending input.
- Consumption for the latest available closed month, with a `period` attribute.
- Meter model and the year of the next verification.
- The time the account data was successfully fetched.

Updates run every six hours. These are billing records, not live electricity
measurements. A new fetch does not mean a new meter reading. Missing data stays
unknown, and unavailable accounts do not report zero consumption.

Monthly corrections can be negative. Historical records do not currently feed
the Energy dashboard: the preview deliberately sets no statistics `state_class`.
Historical statistics need a separate implementation that respects reporting
dates and retrospective corrections.

One meter per household account is supported initially. Returned tariff scales
are kept separate; only the single-zone Odessa case has been verified live.
Business accounts and other regions' personal cabinets are not enabled.

## Add a building address from the cabinet

On the personal account connection, choose **Configure** and select an account.
An independent address setup appears under **Discovered** on the integrations
page. Complete it by confirming the city, street and house against the public
DTEK directory. Recognized address fields are prefilled; ambiguous formats
require manual selection. Apartment numbers are never imported.

An existing address is not duplicated. After creation, the address connection
does not depend on the cabinet: reauthentication, removal or failure of the
personal account connection does not remove its building's monitoring.

The [DTEK Telegram Bot](https://github.com/groove-max/ha-dtek-telegram-bot) remains
an address-based bot. Configure it against the address connection separately.
The preview does not send personal readings, account information or credentials
to Telegram, and does not change existing bot destinations.

## Current limits

The preview cannot submit readings, make payments, create applications or edit
the DTEK profile. To change the selected account set, remove and add the personal
account connection again; existing independent address entries remain intact.
Automatic address synchronization and a personal Telegram assistant are not
part of this version.
