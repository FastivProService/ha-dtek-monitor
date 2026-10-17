# DTEK Monitor for Home Assistant

**English** · [Українська](README.uk.md)

[![HACS custom integration](https://img.shields.io/badge/HACS-custom_integration-41BDF5)](https://hacs.dev/docs/faq/custom_repositories/)
[![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm_Noncommercial-blue.svg)](LICENSE)

Bring DTEK electricity outage schedules, address status and schedule groups into Home Assistant. Supports **Odesa oblast, Kyiv oblast, Dnipropetrovsk oblast and Kyiv city** in Ukraine.

Choose your address once, then use sensors and calendars in dashboards and automations. No Telegram bot or physical electricity sensor is needed to use this integration.

**Want Telegram notifications?** Pair it with [DTEK Telegram Bot](https://github.com/groove-max/ha-dtek-telegram-bot): the companion Home Assistant app sends schedule updates, upcoming-outage reminders and a pinned status message. It can also use your local voltage sensors to check actual power availability.

[Install](#installation) · [Set up](#setup) · [Understand the data](#data) · [Troubleshoot](#troubleshooting) · [Detailed reference](docs/REFERENCE.md)

<a id="features"></a>
## What you get

- The outage status, reason and estimated restoration time reported by DTEK for your address.
- Separate calendars for planned outages and possible outage windows.
- All schedule groups returned for the address, including addresses with multiple groups.
- A clearly labeled **last known schedule** for viewing when DTEK is unavailable.
- Connection diagnostics that distinguish failed requests from an empty published schedule.
- Multiple addresses: add a separate integration entry for each one.

DTEK's address status is **not a physical measurement of electricity in your home**. A scheduled outage, the operator's current status and a local meter reading describe different things. During emergency outages, the published schedule may not apply. This is an independent community project, not an official DTEK product.

<a id="regions"></a>
## Supported operators

Choose the distribution operator serving the address, not just the nearest city.

| Area | Operator | Address lookup |
| --- | --- | --- |
| Odesa oblast | ДТЕК Одеські електромережі — OEM | [Official outage page](https://www.dtek-oem.com.ua/ua/shutdowns) |
| Kyiv oblast | ДТЕК Київські регіональні електромережі — KREM | [Official outage page](https://www.dtek-krem.com.ua/ua/shutdowns) |
| Dnipropetrovsk oblast | ДТЕК Дніпровські електромережі — DNEM | [Official outage page](https://www.dtek-dnem.com.ua/ua/shutdowns) |
| Kyiv city | ДТЕК Київські електромережі — KEM | [Official outage page](https://www.dtek-kem.com.ua/ua/shutdowns) |

Kyiv city and Kyiv oblast use different operators. For Kyiv city, the setup skips city selection. An address within one of these regions may still be served by another distributor; first check that the address is available on the operator's website. Other operators are not currently supported.

<a id="installation"></a>
## Install

Requires **Home Assistant 2026.3.0 or newer** and internet access from Home Assistant to the selected DTEK website. HACS is convenient but optional.

### Through HACS

1. Open **HACS → ⋮ → Custom repositories**.
2. Add `https://github.com/groove-max/ha-dtek-monitor` with type **Integration**.
3. Find **DTEK Monitor** in HACS and download it.
4. Restart Home Assistant.
5. Continue with [address setup](#setup).

This is a [HACS custom repository](https://hacs.dev/docs/faq/custom_repositories/); adding it to HACS installs the files, not the address configuration.

The noncommercial license does not meet the requirements for the default HACS catalog. Install it through **Custom repositories** as described above.

### Manually

1. Download the source from the [release you want to install](https://github.com/groove-max/ha-dtek-monitor/releases).
2. Copy the complete `custom_components/dtek_monitor` folder into your Home Assistant configuration directory, under `custom_components/`.
3. Check that the resulting path is `custom_components/dtek_monitor/manifest.json`, without an extra nested folder.
4. Restart Home Assistant and continue below.

<a id="setup"></a>
## Add your address

1. Open **Settings → Devices & services → Add integration**.
2. Search for **DTEK Monitor**.
3. Choose the operator, settlement, street and house number. Use the spelling offered by DTEK.
4. Keep the initial polling interval at **300 seconds (5 minutes)**.
5. Open the newly created device and check its entities.

To change the polling interval later, open the integration's **Configure** menu. The range is 60–3600 seconds. Shorter intervals cannot make DTEK publish new information sooner.

**First check:** the connection diagnostic should show `ok` after a successful refresh. Compare the address status with the official website, then add the **Outage schedule** calendar to a dashboard calendar card. An empty calendar is not always an error: DTEK may not have published that date yet.

Add another integration entry for another address. You do not need to edit `configuration.yaml`.

<a id="data"></a>
## Understand the entities

Entity names follow your Home Assistant language. Open the integration's device page to find their actual IDs; older installations and renamed entities can have different IDs.

| Entity | How to use it |
| --- | --- |
| **Power** | `on` means DTEK reports no active outage; `off` means DTEK reports an outage. It does not measure your sockets. |
| **Outage status / description** | Reported type and reason: normal, emergency, planned or stabilization outage. |
| **Outage start / end** | Reported start and estimated end; absent values can be normal when there is no active outage. |
| **Primary schedule group / All schedule groups** | The address's group assignments. The first group is not necessarily the only one. |
| **DTEK last update / Schedule changed** | Source update timestamps, not local power-transition times. |
| **Outage schedule** | Published planned outage intervals. A plan is not proof that the power actually went off. |
| **Possible outage schedule** | Possible windows, when available from the source; overlaps with planned intervals are removed. |
| **Last known schedule (view only)** | Saved events with their verification time. Use for reference, not automatic alerts. |
| **DTEK connection** | Connection and freshness diagnostics, including separate address/schedule errors. |

### Multiple groups and hidden group numbers

When DTEK returns several groups, the integration keeps them all and preserves each calendar event's group. It cannot determine which entrance or apartment belongs to which group without additional information.

Some operators, including Kyiv city, do not publish meaningful public group numbers. Internal identifiers may still appear in HA attributes. The companion bot displays these as local **“Variant 1”, “Variant 2”** labels in its Ukrainian messages. These are not official queue numbers and can change when the address assignment changes.

### When a schedule is unavailable

A failed verification makes the live calendars unavailable. The integration does not silently reuse old data as fresh or interpret an unpublished day as “no outages.” Address sensors can still update if only the schedule request failed.

The separate saved calendar remains viewable, including after a restart. It stores concrete events within an eight-day snapshot horizon; it does not invent dates that DTEK has not published or repeat an old weekly template indefinitely. Details: [freshness and automation rules](docs/REFERENCE.md#freshness).

<a id="troubleshooting"></a>
## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Integration is not in “Add integration” | Restart HA after installation; verify the folder path for a manual install. |
| City, street or house is missing | Check the selected operator and the address on its website. Kyiv city is separate from Kyiv oblast. |
| Sensors or live calendars are unavailable | Open **DTEK connection** and inspect `address_error`, `schedule_error` and the last successful refresh. The integration retries automatically. |
| There are no future events | Check whether DTEK has published those dates. Empty or unpublished data does not establish uninterrupted power. |
| DTEK says power is on, but the home is dark | This integration reflects the operator's data. Use a local power sensor with the [Telegram companion](https://github.com/groove-max/ha-dtek-telegram-bot#power-detection) for physical evidence. |
| Telegram messages are missing | Telegram is handled by [DTEK Telegram Bot](https://github.com/groove-max/ha-dtek-telegram-bot), installed separately. |

Do not repeatedly remove and re-add an address to recover from a temporary website failure. See [diagnostics and update guidance](docs/REFERENCE.md#diagnostics).

<a id="support"></a>
## Help, development and releases

- [Report an issue](https://github.com/groove-max/ha-dtek-monitor/issues): include HA/integration versions, operator, expected behavior and sanitized errors. English and Ukrainian reports are welcome.
- [Detailed reference](docs/REFERENCE.md): entity IDs, automation attributes, freshness and backups.
- [Changelog](CHANGELOG.md) and [releases](https://github.com/groove-max/ha-dtek-monitor/releases).
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md).

Licensed under [PolyForm Noncommercial 1.0.0](LICENSE). Noncommercial use, modification and redistribution are permitted under its terms; commercial use is not granted. This is source-available software, not an OSI open-source license. Earlier MIT releases retain their original terms. Dependencies keep their own licenses. See [NOTICE](NOTICE).
