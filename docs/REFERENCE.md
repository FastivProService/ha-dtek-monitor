# DTEK Monitor reference

**English** · [Українська](REFERENCE.uk.md) · [Start here](../README.md)

<a id="entities"></a>
## Find the correct entity IDs

Open **Settings → Devices & services → DTEK Monitor → your device**. Copy entity IDs from there; a display name and an entity ID are different.

New regional entries use a stable prefix such as `dtek_krem_<address_hash>`. Odesa and older entries may retain IDs derived from their original names. Do not rename all entities just to match an example. The Telegram companion discovers supported prefixes automatically.

| Purpose | Typical ID suffix; older IDs can differ |
| --- | --- |
| Reported power state | `binary_sensor.<prefix>_power` |
| Outage classification | `sensor.<prefix>_outage_status` |
| Description, start, end | `sensor.<prefix>_outage_description`, `_outage_start`, `_outage_end` |
| Primary/all groups | `sensor.<prefix>_primary_schedule_group`, `_all_schedule_groups` |
| Source timestamps | `sensor.<prefix>_dtek_last_update`, `_schedule_changed` |
| Connection diagnostics | `sensor.<prefix>_dtek_connection` |
| Planned/possible calendars | `calendar.<prefix>_outage_schedule`, `_possible_outage_schedule` |

Find **Last known schedule (view only)** by its display name. Use live calendars for automation and the saved one for reference.

The reported power sensor uses `on`/`off`; a failed address request makes it unavailable. Outage types are `ok`, `emergency`, `planned` and `stabilization`. If DTEK lists several active outages, the primary status uses the most severe one. Additional `outage_N_*` attributes preserve individual descriptions and times.

<a id="freshness"></a>
## Freshness and calendar automations

A recent HA state change is not enough to establish that a schedule is current. On the group sensor, inspect:

| Attribute | Meaning |
| --- | --- |
| `schedule_groups` | Complete list of address groups; do not assume the scalar primary group is exhaustive. |
| `group_labels_visible` | Whether group identifiers are suitable as public labels. |
| `provider_id`, `address_id` | Source identity; prevents mixing different operators/addresses. |
| `schedule_notifications_ready` | Whether the schedule passed the current address/group verification. |
| `schedule_snapshot_id` | Identity of the verified schedule snapshot. |
| `schedule_coverage` | Published date ranges for each group and interval kind. |

Live calendars and the diagnostic sensor also expose `data_status` (`current`, `stale`, `missing`), `last_verified_at`, `source_updated_at`, `schedule_state` and `schedule_reason`.

For custom schedule notifications:

1. Require `schedule_notifications_ready == true`.
2. Read groups, source identity and snapshot ID, then fetch calendar events.
3. Check that those values still match before sending.
4. Preserve each event's group from its description, such as `Planned; group=GPV3.1`.
5. Treat an empty result as “no planned outages” only when the relevant dates are fully covered for the selected groups.
6. Never use a calendar/event marked `usage=display_only` as fresh notification input.

[DTEK Telegram Bot](https://github.com/groove-max/ha-dtek-telegram-bot) performs these checks for its schedule notifications. Using it avoids having to reproduce this coordination in your own automation.

A calendar can be available while some future dates remain unpublished. `coverage_start`/`coverage_end` bound the stored snapshot; **they do not mean every date inside is published**. Use `schedule_coverage` for publication coverage. A query wholly outside published coverage fails instead of returning an all-clear result.

All schedule calculations use the Kyiv timezone; HA accepts timezone-aware query boundaries, including UTC. Possible windows are shown only when applicable published data exists. Old weekly presets are not projected indefinitely.

### Publication status is different from a request error

**DTEK connection = OK** means the requests and response validation succeeded.
The **Schedule availability** sensor describes today's planned schedule separately:

| State | Meaning |
| --- | --- |
| `current` | A complete verified schedule contains planned outages today. |
| `empty` | Every group has complete dated coverage and no planned outages today. |
| `forecast` | Only a forecast is available; it does not confirm planned outages. |
| `partial` | Coverage is incomplete; missing intervals are not an all-clear. |
| `missing` | No complete applicable schedule was verified. |
| `unpublished` | DTEK explicitly disabled publication. |
| `unsupported` | The address belongs to an unsupported operator. |
| `error` | An address/schedule request failed or returned invalid data. |

`status_date` and `status_timezone` scope the description to a day in Europe/Kyiv.
This describes a schedule, not actual power presence. Publication gaps do not
increase the network failure count or trigger error backoff. Actual failures do.
Live calendar entities still use HA's `unavailable` state when there is no usable
calendar; the separate status explains why. Previously saved events remain for
reference only and never establish an outage-free day.

### Schedule card

For an explicit explanation on a dashboard, add the resource
`/dtek_monitor/schedule-card.js?v=1.8.0b8` as a **JavaScript module** under
**Settings → Dashboards → Resources** (advanced mode). Add a manual card:

```yaml
type: custom:dtek-schedule-card
title: Building — all groups
status_entity: sensor.example_schedule_availability
entities:
  - calendar.example_outage_schedule
  - calendar.example_possible_outage_schedule
```

Use your device's actual entity IDs. For an apartment, use its **My schedule
status** sensor and its personal calendars in a second card. The building card
retains all groups for the bot. This card uses English/Ukrainian UI text and the
built-in HA calendar; it does not change your automations or personal selection.

### Saved schedules

A successful, complete verification stores concrete events in Home Assistant storage for an eight-day horizon. An unchanged successful result refreshes the verification time; a failed request does not. Restoring this snapshot after a restart never makes the live sensors/calendars current. There is no “45-minute fresh data” exception.

<a id="diagnostics"></a>
## Diagnose unavailable data

Open **DTEK connection** on the device page, including its attributes:

- `address_last_success`, `address_error`: address/status requests.
- `schedule_last_success`, `schedule_error`: schedule requests.
- `last_success`, `last_error`, `consecutive_failures`: combined polling health.
- `retry_interval_seconds`: current retry interval in seconds, not a remaining-time countdown.
- `snapshot_storage_error`: problems saving/loading the reference copy.

Repeated failures remain visible in diagnostics even while other entities are unavailable. Failed refreshes back off to roughly 1, 2, 4, 8 and 10 minutes, with small timing variation. Success restores the configured polling interval.

Check the same address on the operator's website. If it also fails, wait for the source to recover. If it works there, inspect HA logs under the integration name for transport, website-protection or parsing errors. Do not reduce the polling interval as a workaround for website protection.

When reporting a problem, include versions, operator, timestamps with timezone and sanitized error text. Logs/screenshots can contain your exact address; remove anything you do not want public.

<a id="updates"></a>
## Updates, backup and removal

1. Make a Home Assistant backup before an upgrade.
2. Read the [changelog](../CHANGELOG.md), update via HACS or replace the integration folder, then restart HA.
3. Verify the connection, address and calendars after the restart. Update the companion bot when its release requires newer integration attributes.

Existing entries migrate automatically; the personal-account preview uses config-entry schema version 4. Address-based identity is designed to preserve entity IDs across upgrades. Manual renames still take precedence. Do not delete and re-add entries as a normal upgrade step.

A full HA backup preserves configuration entries, entity registries and saved snapshots. Do not edit files under `.storage` manually. Removing an integration entry removes its active entities from use; update dependent dashboards, automations and bot addresses accordingly.

<a id="architecture"></a>
## Where the data comes from

The integration reads the selected operator's public shutdowns website. It is not built on a guaranteed public DTEK API contract; website changes can require an update.

| Module | Responsibility |
| --- | --- |
| `providers.py` | Supported operators and their endpoints. |
| `provider.py` | Provider interface and normalized address/schedule types. |
| `dtek_provider.py`, `dtek_client.py` | DTEK website transport and response normalization. |
| `config_flow.py` | Operator/address setup and polling options. |
| `coordinator.py` | Refreshes, verification, retries and diagnostics. |
| `schedule.py`, `snapshot.py` | Interval rules, publication coverage and saved data. |
| `sensor.py`, `binary_sensor.py`, `calendar.py` | HA entities. |

For development and verification, see [Contributing](../CONTRIBUTING.md).
