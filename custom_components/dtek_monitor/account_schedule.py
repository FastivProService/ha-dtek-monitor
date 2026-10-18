"""Explicit private group selections over independent public building schedules."""

from homeassistant.components.calendar import CalendarEntity
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntryState, SIGNAL_CONFIG_ENTRY_CHANGED
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity, DeviceInfo
from homeassistant.util import dt as dt_util

from .account import CONF_ENTRY_KIND, ENTRY_ACCOUNT, account_key
from .const import DOMAIN
from .providers import CONF_PROVIDER, DEFAULT_PROVIDER_ID
from .schedule import KYIV_TZ
from .schedule_status import SCHEDULE_STATUSES, schedule_status, status_attributes

CONF_ACCOUNT_SCHEDULES = "account_schedules"


def address_entries(hass, entry):
    return {
        other.entry_id: other
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.data.get(CONF_ENTRY_KIND, "address") != ENTRY_ACCOUNT
        and other.data.get(CONF_PROVIDER, DEFAULT_PROVIDER_ID)
        == entry.data[CONF_PROVIDER]
    }


def selection(hass, entry, number):
    """Resolve every time: removed/reloaded entries must never leave a stale link."""
    link = entry.options.get(CONF_ACCOUNT_SCHEDULES, {}).get(number, {})
    address = address_entries(hass, entry).get(link.get("address_entry_id"))
    groups = link.get("groups", [])
    coordinator = None
    status = "no_address"
    if address is not None:
        if address.state == ConfigEntryState.LOADED:
            coordinator = getattr(address, "runtime_data", None)
        if coordinator is None or not coordinator.address_success:
            status = "address_unavailable"
        elif not coordinator.address_data.get("group_labels_visible", False):
            status = "labels_hidden"
        elif not groups:
            building_groups = coordinator.address_data.get("schedule_groups", [])
            if len(building_groups) == 1:
                groups = list(building_groups)
                status = "auto_selected"
            else:
                status = "not_selected"
        elif not set(groups).issubset(
            coordinator.address_data.get("schedule_groups", [])
        ):
            status = "group_unavailable"
        else:
            status = "selected"
    return address, coordinator, groups, status


class AccountScheduleEntity(Entity):
    """Listen to the public coordinator without sharing cabinet data with it."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry, number, key):
        self.entry, self.number = entry, number
        identity = account_key(entry.data[CONF_PROVIDER], number)
        self._attr_unique_id = f"account:{identity}:{key}:all"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"account:{identity}")},
            name=f"DTEK • ••••{number[-4:]}",
            manufacturer="DTEK",
            configuration_url="https://my.dtek-oem.com.ua/",
        )
        self._listened = None
        self._unsubscribe_address = None

    @property
    def selected(self):
        return selection(self.hass, self.entry, self.number)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CONFIG_ENTRY_CHANGED, self._entry_changed
            )
        )
        self.async_on_remove(self._disconnect_address)
        self._connect_address()

    @callback
    def _disconnect_address(self):
        if self._unsubscribe_address:
            self._unsubscribe_address()
        self._unsubscribe_address = self._listened = None

    @callback
    def _connect_address(self):
        coordinator = self.selected[1]
        if coordinator is self._listened:
            return
        self._disconnect_address()
        self._listened = coordinator
        if coordinator is not None:
            self._unsubscribe_address = coordinator.async_add_listener(
                self.async_write_ha_state
            )

    @callback
    def _entry_changed(self, change, entry):
        link = self.entry.options.get(CONF_ACCOUNT_SCHEDULES, {}).get(self.number, {})
        if entry.entry_id == link.get("address_entry_id"):
            self._connect_address()
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        address, coordinator, groups, status = self.selected
        return {
            "selection_status": status,
            "selection_source": (
                "building_single_group"
                if status == "auto_selected"
                else "user"
                if groups
                else None
            ),
            "selected_groups": groups,
            "building_groups": (
                coordinator.address_data.get("schedule_groups", [])
                if coordinator
                and coordinator.address_success
                and coordinator.address_data.get("group_labels_visible", False)
                else []
            ),
            "building": address.title if address else None,
        }


class AccountGroupSensor(AccountScheduleEntity, SensorEntity):
    _attr_icon = "mdi:home-lightning-bolt-outline"

    def __init__(self, entry, number):
        super().__init__(entry, number, "account_schedule_groups")

    @property
    def native_value(self):
        # Saved selections stay visible if DTEK later removes them; status and
        # unavailable calendars make the need for verification explicit.
        return ", ".join(self.selected[2]) or None


class AccountScheduleStatusSensor(AccountScheduleEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        "no_address",
        "not_selected",
        "selected",
        "auto_selected",
        "address_unavailable",
        "group_unavailable",
        "labels_hidden",
        "schedule_unavailable",
        *("schedule_" + status for status in SCHEDULE_STATUSES),
    ]

    def __init__(self, entry, number):
        super().__init__(entry, number, "account_schedule_status")

    @property
    def native_value(self):
        _, coordinator, groups, status = self.selected
        if status in ("selected", "auto_selected"):
            publication = schedule_status(coordinator, groups)
            if publication != "current":
                return "schedule_" + publication
        return status

    @property
    def extra_state_attributes(self):
        _, coordinator, groups, _ = self.selected
        return {
            **super().extra_state_attributes,
            **(status_attributes(coordinator, groups) if coordinator else {}),
        }


class AccountScheduleCalendar(AccountScheduleEntity, CalendarEntity):
    _attr_icon = "mdi:calendar-account"

    def __init__(self, entry, number, kind):
        super().__init__(entry, number, "account_" + kind + "_schedule")
        self.kind = kind

    @property
    def available(self):
        _, coordinator, _, status = self.selected
        return (
            status in ("selected", "auto_selected")
            and coordinator.notifications_ready
            and any(c.kind == self.kind for c in coordinator.schedule.coverage)
        )

    @property
    def extra_state_attributes(self):
        _, coordinator, groups, _ = self.selected
        return {
            **(coordinator.snapshot_attributes if coordinator else {}),
            **super().extra_state_attributes,
            "schedule_groups": groups,
            "usage": "personal_schedule",
            "notifications_ready": self.available,
        }

    @property
    def event(self):
        if not self.available:
            return None
        _, coordinator, groups, _ = self.selected
        return coordinator.current_or_next_event(dt_util.now(), groups, self.kind)

    async def async_get_events(self, hass, start_date, end_date):
        if not self.available:
            _, coordinator, _, status = self.selected
            raise HomeAssistantError(
                translation_domain="dtek_monitor",
                translation_key=(
                    "schedule_" + schedule_status(coordinator)
                    if coordinator
                    and status in ("selected", "auto_selected")
                    and not coordinator.notifications_ready
                    else "personal_schedule_unavailable"
                ),
            )
        _, coordinator, groups, _ = self.selected
        start = (
            start_date.replace(tzinfo=KYIV_TZ)
            if start_date.tzinfo is None
            else start_date
        )
        end = end_date.replace(tzinfo=KYIV_TZ) if end_date.tzinfo is None else end_date
        # Empty events are meaningful only when every selected group has
        # publication coverage in the requested range. No unrelated group fallback.
        if coordinator.schedule is None or not all(
            any(
                c.group_id == group
                and c.kind == self.kind
                and c.start < end
                and c.end > start
                for c in coordinator.schedule.coverage
            )
            for group in groups
        ):
            raise HomeAssistantError(
                translation_domain="dtek_monitor",
                translation_key="schedule_range_missing",
            )
        return coordinator.get_events_for_range(start_date, end_date, groups, self.kind)
