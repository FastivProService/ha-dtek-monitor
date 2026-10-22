# Meter readings, energy sensors and reminders

**English** · [Українська](ENERGY_AND_READINGS.uk.md) · [Connection and dashboard](PERSONAL_ACCOUNT.md)

You can enter readings manually. An energy sensor is optional; sending readings always requires a separate confirmation.

- [Home energy sensor settings](#section-1)
- [Send meter readings](#section-2)
- [Use an energy sensor from Home Assistant](#section-3)
- [Scheduled checks without submission](#section-4)
- [Reading reminders in Home Assistant](#section-5)

<a id="section-1"></a>
## Home energy sensor settings

Open **Settings → Home energy sensor** in My DTEK. The sensor setup stays in
this tab; the **Readings** tab contains only the suggestion and an explicit
**Use suggestion** button. **Sensor settings** takes you back to the same setup.
Opening settings reads the current configuration; it never submits meter readings.

- To connect a source, choose **Link an energy sensor**. Select a cumulative
  energy sensor for each tariff zone. Use a secondary-meter estimate with fresh
  official readings, or direct mode when the source is the official meter itself.
- **Advanced settings** contains report freshness, maximum data age and plausible
  input power. Existing values are retained when replacing a binding; direct mode
  does not require a calibration pair.
- **Calibrate against the official meter** captures the current source reading
  alongside the official value you enter. Review the pair and its time, then
  explicitly confirm saving. This only updates the local reference.
- Reference details/history and removing a binding are expandable. A saved
  reference can be calibrated when DTEK is offline; a new binding requires an
  available meter context. Use **DTEK meter data refresh** to retry after recovery.

Draft sensor fields survive ordinary HA updates and tab navigation. Switching
accounts requires discarding an unfinished edit; pending operations block the
switch. Saving/removing a binding retains entered meter values but invalidates
any suggestion from the previous reference. Insert a fresh suggestion or verify
and edit the values manually before reviewing a submission. Local calibration
and a pending reading submission cannot run concurrently within this workspace.

<a id="section-2"></a>
## Send meter readings

Open **My DTEK → Readings**, or use the standalone **DTEK meter readings** card. It performs a real
write to your DTEK account only after you review and explicitly confirm the values.
Opening the card, loading meter data and reviewing values do not send readings.
Use actual meter readings or reviewed source proposals; never use invented values to test the final button.

Choose **DTEK meter readings** in the card picker, or add this manual card
to a private dashboard. No resource registration is needed:

```yaml
type: custom:dtek-account-readings-card
entity: sensor.example_account_data_updated
title: Send my meter readings
```

1. Sign in as an active HA administrator with read and control access to the
   account's entities. Regular users can still use the history card with its
   existing read permissions, but cannot use submission commands.
2. In **My DTEK → Readings**, wait for the meter to load. In the standalone card, choose **Load current meter**. Check the masked account number, full address
   and meter serial number. The account must still be linked in DTEK.
3. Enter the whole-number kWh from your meter for every tariff zone. Values must
   fit the meter's digit limit and cannot be lower than the previous reading.
4. Optionally add a **Meter photo** for each zone. Make sure the official meter’s reading and serial number are legible. For Day/Night, include a separate photo of each tariff display.
5. Choose **Review readings**. Check the previous/new values, differences and photo previews.
6. Tick the confirmation checkbox and choose **Confirm and send to DTEK**.

The confirmation is tied to your HA user and selected account, expires after
five minutes and is usable once. Before sending, the integration checks the
account, meter and previous readings again. Changed data requires a new review.
Regular HA state updates do not overwrite your inputs. The browser keeps your unfinished form in memory; reloading the page discards it.
A reviewed draft is also held temporarily in HA memory for confirmation; no scheduled or automatic submissions
are provided.

Initially this supports one connected household consumption meter with either
one tariff zone or Day/Night. Generation/green tariffs, multiple meters, other
zone schemes, current/pending readings, blocked inputs and unknown meter
metadata require the DTEK cabinet. Use the cabinet for meter rollover,
replacement or corrections. The integration does not invent
a reading date: the observed submission endpoint assigns it on DTEK's side.

### Attach a meter photo

Choose a JPEG, PNG or WebP image up to 20 MB. The browser converts it to JPEG,
removes EXIF metadata, and optimizes it to at most 2560 pixels on its longest
side and 1 MB per photo. Check the resulting preview: the reading, serial number
and tariff must remain clear. You can replace or remove the photo before review.
Ordinary HA updates retain your input and selected images. Switching accounts,
signing out, or reloading the page clears them.

Photos are optional. If you attach photos to a Day/Night submission, attach one
for each zone. The entered readings must match the photos; a home-sensor
suggestion is not a substitute for checking the official meter. This feature is
currently available in the HA readings form; the companion Telegram bot still
submits numerical readings.

**Review readings** transfers a temporary draft to your HA, without sending it
to DTEK. **Confirm and send to DTEK** sends the readings and photos together in
one request. The integration keeps photos in temporary memory, not in the
submission journal or entity states. The journal records only the image type,
size, dimensions and checksum alongside the readings and result.

An accepted reading does **not** confirm that DTEK completed a controller's
inspection or issued an inspection report. Unknown processing responses remain
unconfirmed and are never automatically retried. If DTEK publishes an attachment
or controller comment in reading history, it is shown there; photos can be opened
in the official cabinet. End-to-end photo acceptance is awaiting a real submission.

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
in HA's `.storage`; include it when backing up/restoring this integration.

History caches are invalidated after an attempted submission. An already open
history card and account sensors still need their respective refresh actions;
DTEK may take time to expose the new record. Their previous values are not proof
that a submission failed. Single-zone Odesa submission has been verified against the live service. Kyiv city Day/Night readings and form loading have also been checked live; actual Day/Night submission is covered by automated tests. Check every zone before sending.

<a id="section-3"></a>
## Use an energy sensor from Home Assistant

In **My DTEK → Settings**, open the home energy sensor section. The standalone readings card also includes the energy editor.
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

1. Open **My DTEK → Settings** and its home energy sensor section. Verify the account and meter. Use **Refresh DTEK meter data** if needed.
2. Choose **Link an energy sensor**. In the standalone readings card, first choose **Load / refresh energy source**.
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

In 2.0.0-rc2, the reading review also offers **Save these readings
for calibration** when this meter has an estimate binding. Use this after entering
fresh physical readings that differ from the suggestion:

1. Check every tariff's official reading, sensor value, observation time and correction.
2. Confirm that you read the official meter at the time of those sensor samples.
   Each sample must be no more than three minutes old, including in availability mode.
   Do not pair yesterday's official reading with today's sensor value.
3. Save calibration. Your entered values remain in the form; review them again
   and separately confirm submission to DTEK.

The captured sensor values stay fixed while you confirm. Day and Night keep
independent reference pairs. Canceling changes neither reference. Simply editing
or submitting a number does **not** save a correction. Direct meter bindings do
not offer an offset correction; configure or replace them in sensor settings.
The same optional correction is available in the Telegram bot 0.5.0-rc2.
Calibration from reading review requires Monitor 2.0.0-rc2 and bot 0.5.0-rc2.

To correct an estimate, open **My DTEK → Settings → Calibrate against the official meter**. Enter the actual official readings and choose **Review new reference pair**. The standalone readings card also offers **Use entered readings for local calibration**. Check the proposed pair,
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

This beta provides suggestions, explicit manual submissions and optional HA
reminders. Automatic submissions are not implemented.
These sources belong to personal accounts; they do not configure the building
Telegram bot or its power-presence sensors.

<a id="section-4"></a>
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
picker and choose its account. The integration loads the card automatically.
In My DTEK, the journal is under Settings. YAML example:

```yaml
type: custom:dtek-account-check-card
entity: sensor.example_account_data_updated
title: Apartment — reading checks
```

**Refresh journal** reads only the saved local results; it does not run a check or
contact DTEK. Journal entries and sensor attributes contain estimates and source
entity IDs, so grant access to this account only to the intended HA users.

<a id="section-5"></a>
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


## Readings through a personal Telegram profile

Monitor **2.0.0-rc2** and bot **0.5.0-rc2** let an authorized private chat enter readings or use the energy source configured here. Zone validation, freshness, calibration and duplicate protection remain in the integration. The HA administrator separately grants submission permission to the assigned Telegram user; every send requires confirmation. There is no unattended submission. [Profiles and permissions](PERSONAL_ACCOUNT.md#section-13).
