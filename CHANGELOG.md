# Changelog

All notable changes to this project will be documented in this file.

## [1.8.0b8] - 2026-10-18

- Separate successful responses with missing or disabled schedule publication from request/parsing failures. Keep live notification gates closed until schedules are verified.
- Distinguish a verified outage-free day, incomplete coverage and weekly forecasts in building and personal schedule status. Add an English/Ukrainian schedule card that explains absent data without a generic calendar error.
- Add optional per-account HA reading reminders, once per monthly reading window, with configurable Kyiv time and validated sensor estimates. Restore pending reminders and preserve dismissals across restarts; include an explicit preview button.
- Keep building groups, personal selections and energy references independent. Reminders never submit readings or send Telegram messages.
- Add lifecycle, publication, calendar-boundary and browser regression tests; include the schedule card in CI.

## [1.8.0b7] - 2026-10-18

- Add explicit personal-account links to a building and user-selected outage groups, with separate personal calendars and a translated selection status. A sole verified building group can be selected automatically; multiple groups require a personal choice. Building calendars and Telegram notifications retain all groups.
- Revalidate group membership and publication on every read; removed, hidden or unavailable groups never silently fall back to another building group.
- Count identical HA energy reports as observations and add an explicit availability-based mode for cumulative sources that report only value changes. Preserve the original report timestamp and strict freshness for existing bindings.
- Test zero consumption, connection loss/recovery, source resets, independent account selections, changed groups and address reloads.

## [1.8.0b6] - 2026-10-18

- Add optional personal-account energy sources: direct official meter readings or estimates from a calibrated secondary cumulative meter.
- Save independently confirmed reference pairs and correction history, including fractional kWh, separately from DTEK submission outcomes.
- Add explicit source proposal insertion and local calibration to the English/Ukrainian readings card without overwriting user edits.
- Suspend source proposals on stale data, observed resets, device/unit changes or implausible increases; recheck proposals before submitting.
- Cover corrections, restarts, tariff zones, source permissions and browser interactions with synthetic tests. Scheduled/automatic submissions remain outside this preview.

## [1.7.0] - 2026-10-17

- License new releases under PolyForm Noncommercial 1.0.0; earlier MIT releases keep their original terms.

- Rewrite English/Ukrainian user guides and references; add companion links and a manual GitHub publication checklist.

### Added

- Enable Kyiv city / KEM with a fixed locality and direct street selection. Its flat directory and empty-city API requests stay inside the shared adapter.
- Expose public group-label visibility on HA group sensors while preserving internal IDs for calendar provenance and freshness checks.
- KEM setup, retry, multigroup calendar and publication-visibility runtime tests.

## [1.6.0] - 2026-10-17

### Added

- Enable Dnipropetrovsk oblast / DNEM in operator selection using the shared DTEK adapter. Directory, house selection, multi-group calendars and provider-bound entity identities use the existing contract.
- DNEM setup and HA calendar tests, including revocation to an external CEK operator and mixed DTEK/CEK house records. CEK remains unsupported; its data cannot enable live schedule notifications.

## [1.5.0] - 2026-10-17

### Added

- Operator selection and Kyiv oblast / KREM support through the shared provider adapter, including multi-group addresses. Kyiv city / KEM remains a separate, disabled rollout target.
- Provider-scoped entry identity and stable entity prefixes for new regional addresses, retaining the Telegram addon's expected sensor/calendar suffixes.
- Regression tests for regional setup, directory isolation, disabled operators, retry/cleanup, legacy duplicate detection and bot discovery.

### Compatibility

- Existing Odessa entry, device and entity identifiers and polling options are unchanged. The addon continues to consume HA entities without regional API logic.

## [1.4.0] - 2026-10-17

### Added

- A provider-independent normalized address/schedule interface and a shared DTEK web adapter with per-instance transport settings.
- Offline contract fixtures for OEM, KREM, DNEM and KEM; only OEM is enabled in this first rollout stage. DEM is excluded.
- Explicit publication status, per-group/date coverage, provider/address identity and content revisions.

### Changed

- Config entry version 3 adds the OEM provider without changing existing entity/device identifiers or options. Older display snapshots remain readable.
- The coordinator consumes normalized intervals; wire-format parsing stays in the adapter. Address status remains usable during schedule failures.
- Unpublished weekly templates and incomplete days no longer generate automatic forecasts or empty-calendar claims. Complete daily schedules supersede recurring forecasts.
- Calendar queries entirely outside published coverage fail; publication/coverage changes invalidate pending schedule notifications.

## [1.3.0] - 2026-10-17

### Added

- A persistent, address/provider-bound **Last known schedule (view only)** calendar, including verification time, group provenance, coverage and content identity. It remains usable after an offline restart without enabling automated schedule notifications.
- Concrete cached intervals with an eight-day coverage horizon; cached weekly templates are never extrapolated beyond that horizon.
- Separate address/schedule diagnostics, snapshot storage errors and bounded retry backoff with jitter.
- Group-sensor freshness/revision attributes for notification consumers.

### Changed

- Address status can update while schedule requests fail. Original calendars require complete address/group/schedule verification; an unknown group mapping cannot reuse the previous group as confirmed.
- Only complete successful checks replace persistent snapshots. Successful unchanged checks advance verification time, while failures retain the previous time and events.
- Offline setup with a valid snapshot exposes the display calendar and diagnostics; live entities remain unavailable until verified.

## [1.2.2] - 2026-10-17

### Changed

- Adapted browser-compatible HTTP transport, bounded retries, connection recycling and flexible CSRF extraction from [Zerstoren/ha-dtek-krem-monitor](https://github.com/Zerstoren/ha-dtek-krem-monitor/tree/e1fb7cfd6167a37c6dfedbc385af5c02e6611a45) (MIT). Runtime dependency is pinned to `curl_cffi==0.16.3`.
- Keep cookies in memory across transport recovery; close sessions on unload and on every configuration request, including errors.

### Added

- A diagnostic connection sensor with last successful poll, last error, consecutive failure count and transport/protection counters. It continues updating through repeated failures.

### Fixed

- Treat schedule-check errors as failed polls and roll back partial schedule changes. Cached calendars cannot be queried as current data after a failed poll; no stale-data grace period is enabled.
- Reject malformed JSON and schedule responses rather than interpreting them as unchanged or empty schedules.
- Handle network errors when submitting a city, and normalize street choices.

## [1.2.1] - 2026-10-17

### Fixed

- Normalize range boundaries to Europe/Kyiv before selecting schedule days, including UTC queries across midnight and DST dates.
- Keep calendar windows separate by schedule group and include provenance in their descriptions.
- Canonicalize group lists so API array ordering does not change the compatibility primary-group sensor.
- Reject malformed house payloads as update failures instead of publishing confirmed power availability.

## [1.2.0] - 2026-03-09

### Added

- Automatic registry migration for upgrades from `1.1.x`, moving entities and devices from config-entry-based identifiers to address-based stable identifiers while preserving existing `entity_id` values.
- An options flow for changing the polling interval without removing and re-adding the integration.
- A dedicated sensor for the full list of schedule groups reported by DTEK.
- Regression tests covering HTML schedule extraction, schedule merging, and registry migration helpers.

### Changed

- Refactored schedule handling into a dedicated pure domain layer, centralizing outage merging, overlap resolution, and next-event lookup in a testable module.
- Entity and device registry identifiers are now based on the address `unique_id` instead of the transient config entry ID.
- The primary schedule group is now exposed separately from the full schedule group list.

### Fixed

- Runtime HTTP sessions are now created through Home Assistant helpers, closed correctly on setup failure, and reloaded automatically when options change.
- Preserved all schedule groups reported by DTEK instead of collapsing to the first group, so calendars and attributes no longer silently lose data.
- Corrected fact-vs-preset schedule merging: preset windows are now trimmed around overlapping fact windows rather than only skipping exact duplicates.
- Calendar entities now search the full weekly schedule horizon instead of only looking ahead 48 hours.
- HTML schedule parsing now uses brace matching instead of greedy regex extraction, making it more resilient to site markup changes.

## [1.1.0] - 2026-03-07

### Fixed

- **Stale data after long uptime** — session now automatically refreshes after 1 hour. Previously, the CSRF token and session cookies were never refreshed after the initial connection, causing the integration to serve stale schedule data indefinitely.
- **Schedule data lost after session refresh** — introduced a dirty-flag mechanism so the coordinator picks up new schedule data every time the HTTP session is refreshed, not just on the very first load.
- **Crash when schedule data has unexpected format** — added type guards in `_apply_schedule` and `_get_day_slots` to handle cases where DTEK API returns a list instead of a dict for fact/preset data, preventing `AttributeError: 'list' object has no attribute 'items'`.

## [1.0.0] - 2026-03-06

### Added

- Initial release
- Real-time outage monitoring with binary sensor and status sensors
- Outage type classification: emergency, planned, stabilization
- Two schedule calendars: confirmed outages and possible outages
- Schedule group tracking
- Automatic schedule updates via API polling and HTML parsing
- Multi-outage support with severity-based priority
- 4-step config flow with combo-box selectors
- Full Ukrainian and English translations
- Local brand icons (HA 2026.3+)
