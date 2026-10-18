# Personal account preview

**English** · [Українська](PERSONAL_ACCOUNT_PREVIEW.uk.md)

The `feature/personal-account` branch adds an optional DTEK personal account connection with
read access and explicitly confirmed manual reading submissions. This is an experimental version (`1.8.0b10`), initially for
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

## Several accounts and dashboards

One phone login can contain several personal accounts. **Configure → Select personal
accounts** determines which ones HA monitors. Each selected account has its own HA
device, readings, calendars, energy binding, reference pairs, reminders and check
schedule. Connecting a second account does not copy the first account’s sensor or
calibration. Bind a cumulative grid-import source for the correct apartment.

Dashboard visibility is a separate choice:

- Monitor two accounts and show only one: leave both selected in the integration,
  then add cards for the account you want to see.
- Show both together: add a separate set of cards for each account.
- Use two dashboards or views: select the appropriate account in each card.

The visual editors for the number, history, readings and check-journal cards have
a **Personal account** list with the masked number and address. There is no global
active account. Changing one card never redirects another card’s readings or
changes which accounts the integration monitors. A missing account stays selected
until you explicitly choose another; it never falls back to the first account.
Use **Refresh account list** after adding accounts in the integration settings.
Only connected accounts readable by your HA user appear in this list; to connect
another account, use the integration’s **Select personal accounts** form.

Existing YAML still works: `entity` may be any enabled entity belonging to the
intended account. You can rename HA devices to distinguish apartments. Hiding a
card or dashboard does not revoke access to its entities; HA permissions remain
responsible for access control.

## My apartment group and the building schedules

The cabinet connection does not establish which outage group supplies an apartment.
The verified cabinet responses currently provide no such assignment. A building
may have several groups; its **Primary schedule group** is a compatibility value,
not a claim about your apartment.

1. Use **Configure → Add building monitoring** and finish the discovered address
   setup, or use an existing DTEK address for the same operator.
2. Open **Configure → My group and schedule**, choose the account and verify its
   building address.
3. A building with exactly one verified group is linked automatically. With
   multiple groups, select the group(s) verified for your apartment. You can change
   this choice later. Clearing the selection returns to automatic mode: it works
   only while the building has exactly one verified group.

The account device exposes **My group**, **My schedule status**,
**My outage schedule** and **My possible outage schedule**. Add the personal
calendars to your apartment dashboard; keep the building calendars alongside them
when you want to compare all groups. The building calendars and Telegram bot keep
all address groups. Different accounts can select different groups in one building.

The status distinguishes a manual choice from automatic use of the building’s
sole group. Neither is an apartment assignment returned by the cabinet. If an
automatically linked building gains a second group, only the personal calendar
waits for a selection; the public building calendars and bot keep all groups.
If a manually selected group disappears from the verified building groups, group
labels are hidden, the building is removed or schedule verification fails, the
personal calendars become unavailable. A saved manual choice remains visible for
review; another group is never substituted for it automatically. The schedule status
explains why. Choosing “—” for the building removes only the personal link.

## Account profile: address, EIC and meter

Add `/dtek_monitor/account-profile-card.js?v=1.8.0b10` as a **JavaScript module**
in **Settings → Dashboards → Resources**, then choose **DTEK account profile**
in the card picker. Its visual editor selects one connected account independently
of every other card. A manual example:

```yaml
type: custom:dtek-account-profile-card
entity: sensor.example_account_data_updated
```

The full address, including the apartment when DTEK provides it, is always visible.
The card shows the meter model, tariff scheme, contract power and next verification
year. Expand **Meter details** for manufacture, installation and verification dates,
agreement details, seals and the number of replacement records. For nonempty replacement
histories, open the cabinet to see individual records; their fields are not yet
parsed because no verified example is available.

An **EIC identifies a distribution point**, not a meter number, account number or
outage group. All EICs returned for the account are kept; the current installation's
EIC is labelled separately. These codes do not determine an apartment's outage group.

Account, EIC, meter and seal numbers start masked. Reveal them explicitly and use
**Copy**, or hide them before sharing a screenshot. The address itself stays visible:
check it before sharing. Masking is a display preference; HA read permissions protect
access. Full numbers are not added to entity names or sensor history. The account
profile requires the same account-wide read permission as the history card.

Profile data uses the existing six-hour cabinet refresh. **Refresh profile view**
reads the latest HA cache; use the account device's refresh button to fetch new DTEK
data. Missing optional fields show **Not provided**, not zero. Failure to fetch meter
details does not discard valid readings; old cached data is labelled with its date
when the overall account refresh fails. Profile display does not require permission
or eligibility to submit readings.

### Check whether readings appear in the cabinet

**Check reading history** performs a fresh, rate-limited read. When this HA has a
submission record, it compares every submitted tariff scale with the cabinet's
active-energy records for the same reading date in Kyiv. The result distinguishes
all scales matching, partial matches, multiple possible matches and no match yet.
Rows retain DTEK's active/inactive/blocked status and source. An unmatched record is
not proof of rejection; DTEK may publish later or assign another reading date.

A match is evidence of a corresponding account record, **not proof that this specific
request created it or that a monthly settlement is final**. Historical rows do not
identify their individual meter. A known change from the submitted meter is reported
separately. Checking never repeats a submission, changes calibration or acknowledges
an uncertain delivery. If this HA has no submission journal, it shows recent cabinet
records for the current year without claiming it submitted them.

### Optional meter notifications

Open **Configure → Meter verification and replacement**, select the account and
enable either notification. Both are off by default and independent of reading
reminders. These are local HA notifications, visible to users of that HA, with a
masked account number; no private account data is sent to the building bot.

- **Verification:** one notice in the listed year, then one per following calendar
  year until DTEK updates the information. A year alone cannot establish the exact
  deadline or whether verification is overdue; check with the operator.
- **Meter number change:** establishes a baseline on the first successful observation
  and reports later changes, including after a restart. Missing metadata never resets
  the baseline. A change can mean a replacement or a cabinet correction. Check the new
  meter and explicitly set new energy reference readings; old bindings are not moved.

Notices follow the six-hour profile refresh. Dismissal survives restarts. Disabling
a notification removes it; re-enabling verification may show the current year's
notice again. Changes observed while replacement notices are disabled establish a
new baseline rather than a backlog of alerts.

## History and monthly chart

The bundled **DTEK account history** card shows an account's readings and monthly
consumption for a selected year. Each card belongs to one account. It uses your
Home Assistant session and requires permission to read the configured entity
and all sensors of that account; the browser never receives your DTEK password or API tokens.

1. Enable **Advanced mode** in your HA user profile if Resources is hidden.
2. Open **Settings → Dashboards → Resources** (possibly under the three-dot menu).
3. Add `/dtek_monitor/account-history-card.js?v=1.8.0b10` as a **JavaScript module**.
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

### Compare the same periods

Choose a year and press **Compare with previous year**. The card compares monthly,
yearly and seasonal official consumption using only months that are closed and
published in **both** years. A partial yearly/seasonal total explicitly lists its
included months. Missing periods are not zero; negative DTEK corrections are retained.
Winter is explicitly grouped as January, February and December of the same calendar
year, not a continuous December–February season. Both fetch dates are shown.

These are dated **consumption** figures from DTEK, not cumulative meter readings and
not estimates from the bound HA source. The comparison fetches the preceding year's
history on demand and uses the same cache and rate limits as other history reads.

## Show or hide the account number

The history and submission cards include an eye button next to **Account number**.
Numbers start masked. Choose **Show account number** to request the full number
using your HA session; choose **Hide account number** before taking a screenshot.
This works for users with permission to read all sensors of that selected account,
without requiring administrator rights. Submission still requires an administrator.

This is a display convenience, not password protection. The full number is not
added to sensor states, attributes, device names or browser storage. Hiding it
clears the displayed value; rebuilding/closing the card masks it again. Toggling
the eye does not reset reading inputs, selection of the year or pagination.
The eye controls the account number only; an address or meter serial shown on
the same card is still visible.

For a dashboard using standard cards, add this compact card alongside them:

```yaml
type: custom:dtek-account-number-card
entity: sensor.example_account_data_updated
```

It is loaded automatically by either DTEK custom card resource above. If you use
neither, register `/dtek_monitor/account-number.js?v=1.8.0b10` as a JavaScript module
in Resources. Existing entity IDs and masked device labels are unchanged.

## Send meter readings

This preview includes a separate **DTEK meter readings** card. It performs a real
write to your DTEK account only after you review and explicitly confirm the values.
Opening the card, loading meter data and reviewing values do not send readings.
Use actual meter readings or reviewed source proposals; never use invented values to test the final button.

Add `/dtek_monitor/account-readings-card.js?v=1.8.0b10` as another **JavaScript module**
in dashboard Resources, then add this manual card to a private dashboard:

```yaml
type: custom:dtek-account-readings-card
entity: sensor.example_account_data_updated
title: Send my meter readings
```

1. Sign in as an active HA administrator with read and control access to the
   account's entities. Regular users can still use the history card with its
   existing read permissions, but cannot use submission commands.
2. Choose **Load current meter**. Check the masked account number, full address
   and meter serial number. The account must still be linked in DTEK.
3. Enter the whole-number kWh from your meter for every tariff zone. Values must
   fit the meter's digit limit and cannot be lower than the previous reading.
4. Choose **Review readings**. Check the previous/new values and differences.
5. Tick the confirmation checkbox and choose **Confirm and send to DTEK**.

The confirmation is tied to your HA user and selected account, expires after
five minutes and is usable once. Before sending, the integration checks the
account, meter and previous readings again. Changed data requires a new review.
Regular HA state updates do not overwrite your inputs. Drafts are held only in
memory and disappear on connection reload; no scheduled or automatic submissions
are provided.

Initially this supports one connected household consumption meter with either
one tariff zone or Day/Night. Generation/green tariffs, multiple meters, other
zone schemes, current/pending readings, blocked inputs and unknown meter
metadata require the DTEK cabinet. Use the cabinet for meter rollover,
replacement, corrections or photo attachments. The integration does not invent
a reading date: the observed submission endpoint assigns it on DTEK's side.

[DTEK's FAQ](https://www.dtek-oem.com.ua/ua/faq) explains that readings submitted
outside the monthly reporting window are recorded on the submission date. They
can still be used in estimated billing calculations. There is no separate
"monthly reading" flag in the observed request. The integration does not turn
the difference between readings into a closed month's consumption.

A successful HTTP response alone is not treated as acceptance. The card reports
acceptance only when DTEK explicitly confirms every submitted zone. This is
receipt of readings, not a final billing calculation. Partial responses, network
failures and unrecognized results require checking the cabinet. POST requests
are never automatically retried, including after authentication errors.

Before sending, HA atomically saves a private attempt record containing the time,
values and status, keyed by a hashed account identifier. It survives restart;
an interrupted request remains unresolved. If a result is unknown or partial,
choose **Check submission status**, inspect every zone in the DTEK cabinet and
only then use **Unlock new entry after checking**. Unlocking sends nothing and
does not prove rejection. New values still require a fresh review/confirmation;
pending DTEK readings can continue to prevent entry. Do not delete the local
attempt record to retry an uncertain request. The latest attempt is retained
in HA's `.storage`; include it when backing up/restoring this preview.

History caches are invalidated after an attempted submission. An already open
history card and account sensors still need their respective refresh actions;
DTEK may take time to expose the new record. Their previous values are not proof
that a submission failed. Submission outcomes have synthetic tests. One live submission of actual
single-zone readings has also been verified with the owner’s authorization:
DTEK confirmed acceptance and history contained an active record dated on submission.

## Use an energy sensor from Home Assistant

The readings card includes an optional **Home Assistant energy source** panel.
It supports registered cumulative energy sensors (`total` or `total_increasing`)
in Wh, kWh or MWh. Connect the device to HA using its existing integration first;
DTEK Monitor does not add a device driver. An active HA administrator needs access
to the account and each source sensor.

Choose the sensor for **this apartment and this meter**. A secondary meter must
measure the same grid-import energy as the official meter, including all loads.
Do not bind another apartment's sensor, inverter output, net import/export energy,
or a sensor that counts only selected appliances. The integration cannot detect
incorrect wiring or an unreported bypass. Daily/monthly resetting counters and
energy derived only from power samples are not suitable for this initial feature.

Two modes are available:

| Mode | Proposed cumulative reading |
| --- | --- |
| Read the official meter directly | The corresponding meter register from HA |
| Estimate from a secondary meter | Official reference + current source − source reference |

For example, an official reading of `1000` paired with a source reading of `100`
produces an estimate of `1025` when the source reaches `125`. If you check the
official meter and it actually shows `1030`, save the new pair `1030 / 125`.
At source reading `135`, the next estimate is `1040`. The previous 5 kWh gap is
already included; it is not added again. This corrects a past difference, not an
ongoing proportional measurement error. Repeated discrepancies need investigation.

### Create a binding

1. Load the current DTEK meter in the readings card and verify its account,
   address and serial number.
2. In the energy panel, choose **Load / refresh energy source**, then
   **Link an energy sensor**.
3. Choose the mode and source entity for each tariff zone. Day/Night requires two
   separate, correctly mapped cumulative registers; one total cannot be split
   retrospectively across a gap in HA data.
4. Choose how to check the source. **Require recent reports** is the default;
   identical repeated HA reports count as fresh, even during zero consumption.
   **Trust the integration’s availability status** is for sources that report
   only counter changes: use it only when the source integration marks connection
   loss as `unavailable`. This mode cannot detect an integration stuck on a numeric
   value and does not apply the age limit. Set the maximum data age for report
   mode and the maximum plausible input power. Defaults are
   15 minutes and 30 kW. The power check permits 1 kWh of reporting/quantization
   tolerance; it is an anomaly check, not a meter accuracy guarantee.
5. In estimate mode, enter the physical official reading taken at the same time
   as the source sample. Decimal kWh are allowed here, using a dot.
6. Choose **Review binding**, check the displayed readings and source timestamp,
   tick the confirmation and choose **Save local reference**.

A new/replaced binding verifies the available meter with DTEK. If the cabinet
blocks meter input, try creating the binding later. An existing reference can
still be viewed and calibrated locally while DTEK is unavailable.

### Use a proposal or correct a reference

**Load / refresh energy source** refreshes the proposal without changing your
submission inputs. **Use proposed readings in the form** deliberately inserts
it. Decimal kWh remain in the calculation; only the whole-number part is inserted
for submission. Review and confirm the DTEK submission separately. Source-based
proposals expire after three minutes and are checked again before sending.
Editing the inserted number changes it to a manual reading.

To correct an estimate, enter the actual official readings in the main form and
choose **Use entered readings for local calibration**, or enter them in the
energy panel and choose **Review new reference pair**. Check the proposed pair,
sample time and correction, then confirm the local save. The captured source
reference is kept even if the sensor advances while you review it.

Saving a reference never submits readings. It survives a rejected, failed or
unattempted DTEK submission. Conversely, submitting a calculated reading never
moves the reference pair or discards fractional consumption. The panel retains
the latest 24 reference changes; each tariff zone has its own reference.

Unavailable sources cannot supply a proposal in either mode; sources with old
reports are also blocked in the default report mode. A counter decrease,
unexpected increase, changed unit or changed device identity suspends proposals.
Observed resets remain flagged after a rebound or HA restart. After a verified
reset, create a new reference pair; after a device/unit change, replace the
binding. Direct reading mode requires replacing the binding after a reset.
A communication gap can be recovered from cumulative endpoints if the device
kept counting without a reset. Resets or bypass consumption while HA was offline
cannot always be detected; a physical meter check is needed when continuity is
uncertain.

The displayed timestamp is HA's last report, including an unchanged value, not proof
that a cloud service obtained a fresh device measurement. Configure an age limit
appropriate for the device's reporting interval and verify its reporting behavior.
An unchanged energy value can legitimately mean no consumption. Availability
mode preserves the original report time; it does not present an old report as new.
Both LocalTuya and Tuya Local sources work through the same standard HA energy
sensor interface; no Tuya driver or developer-account setup is part of DTEK Monitor.

References and continuity checkpoints are stored independently of Recorder in
HA's private `.storage/dtek_monitor.energy.<entry_id>` file. Include it in backups.
Storage errors disable source proposals; manual entry remains available. Removing
a binding deletes its local calibration history and does not modify DTEK data.

This preview provides suggestions, explicit manual submissions and optional HA
reminders. Automatic submissions are not implemented.
These sources belong to personal accounts; they do not configure the building
Telegram bot or its power-presence sensors.

## Scheduled checks without submission

Open **Configure → Scheduled reading checks**, choose an account, and enable checks.
They are off by default. Select a time in **Europe/Kyiv** and either every day or
each day in the reporting window (last two days of the month and first three days
of the next month). The minute timer runs the check at the first opportunity at or
after the selected time. HA catches up only for the current day after downtime;
it never replays checks for earlier days.

A check reads the current DTEK meter context and calculates the proposal from that
account’s bound cumulative energy source. It checks tariff zones, meter identity,
previous readings, source freshness, observed resets, replacement and implausible
jumps. Pending/unknown submission outcomes block the proposal. Each tariff zone
keeps its own source and value. An account without a source reports that it needs
one; it does not borrow another account’s source.

The proposed value is the **cumulative meter reading to submit**, not monthly consumption. In estimate mode it is the saved official reading plus the source counter’s increase since calibration. Whole kWh are rounded down for submission; fractions remain in the calculation. Monthly consumption requires the difference between readings at the month’s boundaries.

**Nothing is sent to DTEK.** A check does not create a submission confirmation,
change the local reference pair, or issue Telegram messages. Automatic submission
is not available in this version. A ready result means the estimate passed the
checks **at the displayed time**; review the physical meter and use the normal
readings card for an explicitly confirmed submission with fresh validation.

Each account device has **Check readings without submission** and **Reading check**.
The button works with the schedule disabled and does not consume the scheduled
check. Manual checks share a 60-second limit within a connection. The sensor keeps
the latest result and up to 20 checks, including original sensor report timestamps.
The journal survives restarts. An interrupted scheduled check is marked as such,
without repeating that day; journal errors stop checks until resolved.

For a readable journal on a dashboard, add **DTEK reading checks** in the card
picker and choose its account. Loading the readings card resource also registers
this card. For a standalone journal, register
`/dtek_monitor/account-check-card.js?v=1.8.0b10` as a JavaScript module. YAML example:

```yaml
type: custom:dtek-account-check-card
entity: sensor.example_account_data_updated
title: Apartment — reading checks
```

**Refresh journal** reads only the saved local results; it does not run a check or
contact DTEK. Journal entries and sensor attributes contain estimates and source
entity IDs, so grant access to this account only to the intended HA users.

## Reading reminders in Home Assistant

Open **Configure → Reading reminders**, choose an account and enable reminders.
They are off by default. Set the time in **Europe/Kyiv** (default 19:00) and
whether to include a reading estimate from the linked energy sensor.

One reminder is created per reporting period, on the first available day at/after
the chosen time within the **last two days of the month and first three days of
the next month**, following [DTEK Odesa's recommendation](https://www.dtek-oem.com.ua/ua/news/yak-shvidko-i-prosto-peredati-pokazi-lichilnika-za-svitlo-rozpovidaye-dtek-odeski-elektromerezhi). Short months,
leap years and year boundaries are handled automatically. If HA was offline, it
catches up within this window; it does not issue a missed reminder after it closes.
This window does not mark an estimate or a prior reading as a final monthly value.

The reminder appears in the HA notification drawer, visible to users of this HA;
it is not a private mobile push or Telegram message. Only the last four account
digits are displayed. An included estimate is checked against source availability,
resets, the current meter identity and recorded reading. If it cannot be verified,
the reminder asks you to read the meter manually. A reminder never submits data.
Open the readings card, check/edit the values and confirm a submission yourself.

Dismiss the reminder if you have already submitted through any channel. Dismissal
is remembered for the whole period, even after a restart. An undismissed reminder
is restored within the window; disabling reminders removes it. There is no daily
notification campaign. Use **Preview reading reminder** on the account device to
check its appearance at any time; a preview neither enables reminders nor consumes
the scheduled reminder for that period.

For calendar presentation, use the [schedule card](REFERENCE.md#schedule-card).
It distinguishes verified empty days, missing publication and actual request
errors for both the building and the optional personal calendars.

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

The preview cannot make payments, create applications or edit
the DTEK profile. Change the selected account set in the connection settings; existing
independent address entries remain intact.
Automatic address synchronization and a personal Telegram assistant are not
part of this version.
