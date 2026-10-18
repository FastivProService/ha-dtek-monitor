"""Calendar platform for DTEK Monitor — outage schedule."""

from __future__ import annotations

import datetime as dt

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .coordinator import DTEKDataCoordinator
from .schedule import KYIV_TZ
from .account import CONF_ENTRY_KIND, ENTRY_ACCOUNT, CONF_ACCOUNTS
from .schedule_status import status_attributes, schedule_status
from .helpers import build_device_info, build_entity_unique_id, suggested_entity_id

_CALENDAR_TYPES: list[tuple[str, str, str]] = [
    # (event_type, translation_key, unique_id_suffix)
    ("outage", "outage_schedule", "outage_schedule"),
    ("possible", "possible_schedule", "possible_schedule"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up DTEK calendars from a config entry."""
    if entry.data.get(CONF_ENTRY_KIND) == ENTRY_ACCOUNT:
        from .account_schedule import AccountScheduleCalendar

        async_add_entities(
            [
                AccountScheduleCalendar(entry, number, kind)
                for number in entry.data[CONF_ACCOUNTS]
                for kind in ("outage", "possible")
            ]
        )
        return
    coordinator: DTEKDataCoordinator = entry.runtime_data
    async_add_entities(
        [
            *(
                DTEKScheduleCalendar(
                    coordinator, entry, event_type, translation_key, uid_suffix
                )
                for event_type, translation_key, uid_suffix in _CALENDAR_TYPES
            ),
            DTEKSnapshotCalendar(coordinator, entry),
        ]
    )


class DTEKScheduleCalendar(CoordinatorEntity[DTEKDataCoordinator], CalendarEntity):
    """Calendar entity representing a DTEK outage schedule for an address."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-alert"

    def __init__(
        self,
        coordinator: DTEKDataCoordinator,
        entry: ConfigEntry,
        event_type: str,
        translation_key: str,
        uid_suffix: str,
    ) -> None:
        """Initialize the calendar entity."""
        super().__init__(coordinator)
        self._event_type = event_type
        self._attr_translation_key = translation_key
        self._attr_unique_id = build_entity_unique_id(entry, uid_suffix)
        self.entity_id = suggested_entity_id(entry, "calendar", uid_suffix)
        self._attr_device_info = build_device_info(entry)

    @property
    def available(self) -> bool:
        return self.coordinator.notifications_ready and any(
            c.kind == self._event_type for c in self.coordinator.schedule.coverage
        )

    @property
    def extra_state_attributes(self) -> dict:
        return {
            **self.coordinator.snapshot_attributes,
            **status_attributes(self.coordinator),
            "usage": "automations",
        }

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current or next upcoming event."""
        if not self.coordinator.notifications_ready or self.coordinator.data is None:
            return None

        groups = self.coordinator.data.get("schedule_groups", [])
        if not groups:
            return None

        return self.coordinator.current_or_next_event(
            dt_util.now(),
            groups,
            self._event_type,
        )

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: dt.datetime,
        end_date: dt.datetime,
    ) -> list[CalendarEvent]:
        """Return calendar events within a datetime range."""
        if not self.coordinator.notifications_ready or self.coordinator.data is None:
            raise HomeAssistantError(
                translation_domain="dtek_monitor",
                translation_key="schedule_" + schedule_status(self.coordinator),
            )

        groups = self.coordinator.data.get("schedule_groups", [])
        if not groups:
            return []

        schedule = self.coordinator.schedule
        start = (
            start_date.replace(tzinfo=KYIV_TZ)
            if start_date.tzinfo is None
            else start_date
        )
        end = end_date.replace(tzinfo=KYIV_TZ) if end_date.tzinfo is None else end_date
        if schedule is None or not all(
            any(
                c.group_id == group
                and c.kind == self._event_type
                and c.end > start
                and c.start < end
                for c in schedule.coverage
            )
            for group in groups
        ):
            raise HomeAssistantError(
                translation_domain="dtek_monitor",
                translation_key="schedule_range_missing",
            )

        return self.coordinator.get_events_for_range(
            start_date,
            end_date,
            groups,
            self._event_type,
        )


class DTEKSnapshotCalendar(CoordinatorEntity[DTEKDataCoordinator], CalendarEntity):
    """Explicit display-only view of finite, previously verified events."""

    _attr_has_entity_name = True
    _attr_translation_key = "last_known_schedule"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: DTEKDataCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = build_entity_unique_id(entry, "last_known_schedule")
        self.entity_id = suggested_entity_id(entry, "calendar", "last_known_schedule")
        self._attr_device_info = build_device_info(entry)

    @property
    def available(self) -> bool:
        return self.coordinator.snapshot is not None

    @property
    def extra_state_attributes(self) -> dict:
        return {
            **self.coordinator.snapshot_attributes,
            "usage": "display_only",
            "notifications_ready": False,
        }

    def _event(self, window) -> CalendarEvent:
        snapshot = self.coordinator.snapshot
        assert snapshot is not None
        checked = snapshot.checked_at.astimezone(KYIV_TZ).strftime("%d.%m.%Y %H:%M")
        status = (
            "Перевірений"
            if self.coordinator.notifications_ready
            else "Актуальність не підтверджена"
        )
        return CalendarEvent(
            start=window.start,
            end=window.end,
            summary=f"[Збережений графік] {window.summary}",
            description=(
                f"usage=display_only; {status}. Перевірено: {checked}. "
                f"Лише для перегляду, не для сповіщень. {window.description}"
            ),
        )

    @property
    def event(self) -> CalendarEvent | None:
        snapshot = self.coordinator.snapshot
        if snapshot is None:
            return None
        now = dt_util.now()
        window = next((w for w in snapshot.windows if w.end > now), None)
        return self._event(window) if window else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: dt.datetime, end_date: dt.datetime
    ) -> list[CalendarEvent]:
        snapshot = self.coordinator.snapshot
        if snapshot is None:
            raise HomeAssistantError("No previously verified DTEK schedule is stored")
        start = (
            start_date.replace(tzinfo=KYIV_TZ)
            if start_date.tzinfo is None
            else start_date
        )
        end = end_date.replace(tzinfo=KYIV_TZ) if end_date.tzinfo is None else end_date
        return [self._event(w) for w in snapshot.events_between(start, end)]
