# Personal account preview

**English** · [Українська](PERSONAL_ACCOUNT_PREVIEW.uk.md)

The `feature/personal-account` branch adds an optional, read-only DTEK personal
account connection. This is an experimental version (`1.8.0b3`), initially for
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

## Change accounts and refresh

Choose **Configure → Select personal accounts** on the personal account connection.
The list is fetched when the form opens and stays unchanged while you edit it.
Keep at least one account selected. Saving reloads only this connection. Entities
and devices belonging only to deselected accounts are removed; automations using
those entities will need updating. The DTEK website's linked accounts are not changed.

Each account device has a **Refresh all accounts** button. It updates all selected
accounts in that connection. A shared 60-second limit applies after any refresh
attempt, including automatic refreshes and failures. A failure leaves affected
sensors unavailable; successfully refreshed accounts remain usable. The time of
fetching data is distinct from the date of the recorded reading.

## History and monthly chart

The bundled **DTEK account history** card shows an account's readings and monthly
consumption for a selected year. Each card belongs to one account. It uses your
Home Assistant session and requires permission to read the configured entity
and all sensors of that account; the browser never receives your DTEK password or API tokens.

1. Enable **Advanced mode** in your HA user profile if Resources is hidden.
2. Open **Settings → Dashboards → Resources** (possibly under the three-dot menu).
3. Add `/dtek_monitor/account-history-card.js?v=1.8.0b3` as a **JavaScript module**.
   When upgrading, edit this existing resource URL instead of adding another copy.
4. Add a manual card to a private dashboard with the configuration below.
   Replace the entity with **Account data updated** from the desired account device.

```yaml
type: custom:dtek-account-history-card
entity: sensor.example_account_data_updated
title: My DTEK account
```

Choose a year in the card; optionally set `year: 2025` in its configuration to
start with that year. The default is the current year in Kyiv. The month table
below the chart also shows returned tariff-zone values. The readings table has
ten rows per page and shows Kyiv dates, values, scale, energy type, source and
status. **Active**, **inactive**, **blocked** and **unknown** reflect DTEK's flags;
inactive does not necessarily mean a rejected submission.

Only closed months are plotted. Zero is a real returned value; a gap means no
data was returned. Open months are labeled separately. Negative corrections
remain negative. The API does not identify the meter for each historical row,
so the integration does not associate old readings with the current meter or
calculate consumption from their differences across replacements.

History loads when the card opens or the year changes. Results are cached in
memory for up to six hours, with at most 16 account/year combinations per
connection. **Refresh history** requests a replacement result, including revised
past months; it is separate from the device's **Refresh all accounts** button.
Repeated history fetches for the same account/year are limited to once per
60 seconds. A DTEK rate-limit response pauses history requests for five minutes.
After a failed refresh, any retained result is explicitly labeled with its
original fetch time. Reloading the connection clears the cache.

History is not stored as sensor attributes or imported into Energy statistics.
An empty year is shown explicitly. More than 1,000 returned rows or 20 API pages,
inconsistent pagination, or malformed data produces an error instead of a
silently truncated history. Older years are available only when DTEK returns them.

## Dashboard card

Add a manual card to a private dashboard. Replace every example entity ID below
with the IDs from your account device. This uses only built-in Home Assistant
cards. Duplicate the card for additional accounts; add reading/date rows for
each tariff scale when needed.

```yaml
type: entities
title: DTEK personal account
show_header_toggle: false
entities:
  - entity: sensor.example_recorded_reading
    name: Recorded reading
  - entity: sensor.example_reading_date
    name: Reading date
  - entity: sensor.example_latest_closed_month_consumption
    name: Monthly consumption
  - type: attribute
    entity: sensor.example_latest_closed_month_consumption
    attribute: period
    name: Consumption month
  - entity: sensor.example_account_data_updated
    name: Data fetched
  - entity: sensor.example_meter_model
    name: Meter model
  - entity: sensor.example_next_verification_year
    name: Next verification year
  - entity: button.example_refresh_all_accounts
    name: Refresh all accounts
```

## Add a building address from the cabinet

On the personal account connection, choose **Configure → Add building monitoring** and select an account.
An independent address setup appears under **Discovered** on the integrations
page. Complete it by confirming the city, street and house against the public
DTEK directory. Recognized address fields are prefilled; ambiguous formats
require manual selection. Apartment numbers are never imported.

An existing address is not duplicated. After creation, the address connection
does not depend on the cabinet: reauthentication, removal or failure of the
personal account connection does not remove its building's monitoring.

If DTEK confirms the address but has no usable schedule, the address connection
still loads. **Schedule availability** explains the current state; live calendars
remain unavailable until a schedule is verified. Automatic retries continue.
No missing day is interpreted as a day without outages, and a previously saved
schedule remains a separate view-only calendar.

The [DTEK Telegram Bot](https://github.com/groove-max/ha-dtek-telegram-bot) remains
an address-based bot. Configure it against the address connection separately.
The preview does not send personal readings, account information or credentials
to Telegram, and does not change existing bot destinations.

## Current limits

The preview cannot submit readings, make payments, create applications or edit
the DTEK profile. Change the selected account set in the connection settings; existing
independent address entries remain intact.
Automatic address synchronization and a personal Telegram assistant are not
part of this version.
