"""Private account sensors. Deliberately no outage/bot entity names or statistics."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .account import CONF_ACCOUNTS, account_key
from .const import DOMAIN
from .providers import CONF_PROVIDER


def account_sensors(coordinator, entry):
    """Add late-appearing scales/accounts as data recovers after setup."""
    created = set()

    def discover():
        entities = []
        for number in entry.data[CONF_ACCOUNTS]:
            snapshot = (coordinator.data or {}).get(number)
            keys = [
                (key, None)
                for key in (
                    "account_consumption",
                    "account_meter_model",
                    "account_verification_year",
                    "account_updated",
                )
            ]
            if snapshot:
                keys += [
                    (key, reading.zone)
                    for reading in snapshot.readings
                    for key in ("account_reading", "account_reading_date")
                ]
            for key, zone in keys:
                identity = (number, key, zone)
                if identity not in created:
                    created.add(identity)
                    entities.append(
                        AccountSensor(coordinator, entry, number, key, zone)
                    )
        return entities

    return discover


class AccountSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, number, key, zone=None):
        super().__init__(coordinator)
        self.number = number
        self.key = key
        self.zone = zone
        identity = account_key(entry.data[CONF_PROVIDER], number)
        self._attr_unique_id = f"account:{identity}:{key}:{zone or 'all'}"
        self._attr_translation_key = key
        if zone == "GENERAL":
            self._attr_translation_key = key + "_general"
        elif zone:
            self._attr_translation_placeholders = {"zone": zone}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"account:{identity}")},
            name=f"DTEK • ••••{number[-4:]}",
            manufacturer="DTEK",
            configuration_url="https://my.dtek-oem.com.ua/",
        )
        if key in ("account_consumption", "account_reading"):
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            # These are dated billing records, not present-time energy samples.
            # No state_class: never create misleading Energy statistics.
        elif key == "account_reading_date":
            self._attr_device_class = SensorDeviceClass.DATE
        elif key == "account_updated":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        else:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def snapshot(self):
        return (self.coordinator.data or {}).get(self.number)

    @property
    def reading(self):
        if self.snapshot:
            return next(
                (r for r in self.snapshot.readings if r.zone == self.zone), None
            )
        return None

    @property
    def available(self):
        return (
            super().available
            and self.snapshot is not None
            and (self.zone is None or self.reading is not None)
        )

    @property
    def native_value(self):
        snapshot = self.snapshot
        if snapshot is None:
            return None
        if self.key == "account_reading":
            return self.reading.value if self.reading else None
        if self.key == "account_reading_date":
            return self.reading.read_at if self.reading else None
        return {
            "account_consumption": snapshot.consumption,
            "account_meter_model": snapshot.model,
            "account_verification_year": snapshot.verification_year,
            "account_updated": snapshot.fetched_at,
        }.get(self.key)

    @property
    def extra_state_attributes(self):
        if self.key == "account_consumption":
            return {
                "period": self.snapshot.consumption_period if self.snapshot else None
            }
        if self.key == "account_reading":
            return {
                "zone": self.zone,
                "reading_date": (
                    self.reading.read_at.isoformat() if self.reading else None
                ),
            }
        return {}
