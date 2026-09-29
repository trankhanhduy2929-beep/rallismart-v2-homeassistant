"""RalliSmart sensor platform (Rang Dong HC v1 sensors)."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    PERCENTAGE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .control import (
    ATTR_HUMIDITY,
    ATTR_PM25,
    ATTR_TEMP,
    sensor_kind,
)
from .coordinator import RalliSmartCoordinator

_LOGGER = logging.getLogger(__name__)

# kind -> (attr_id, device_class, unit, state_class)
KINDS: dict[str, tuple[int, SensorDeviceClass | None, str | None, SensorStateClass | None]] = {
    "temperature": (ATTR_TEMP, SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS,
                    SensorStateClass.MEASUREMENT),
    "humidity": (ATTR_HUMIDITY, SensorDeviceClass.HUMIDITY, PERCENTAGE,
                 SensorStateClass.MEASUREMENT),
    "pm25": (ATTR_PM25, SensorDeviceClass.PM25,
             CONCENTRATION_MICROGRAMS_PER_CUBIC_METER, SensorStateClass.MEASUREMENT),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: RalliSmartCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = []
    for uid, item in (coordinator.data or {}).items():
        kind = sensor_kind(item["device"])
        if kind and kind[1] == "sensor" and kind[0] in KINDS:
            entities.append(RalliSmartSensor(coordinator, uid, kind[0]))
    async_add_entities(entities)


class RalliSmartSensor(CoordinatorEntity[RalliSmartCoordinator], SensorEntity):
    def __init__(self, coordinator: RalliSmartCoordinator, uid: str, kind: str) -> None:
        super().__init__(coordinator)
        self._uid = uid
        self._kind = kind
        self._attr_unique_id = f"rallismart_{uid}_{kind}"
        base = coordinator.data[uid]["device"].get("name")
        self._attr_name = f"{base} {kind}"
        attr_id, device_class, unit, state_class = KINDS[kind]
        self._attr_id = attr_id
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class

    @property
    def native_value(self):
        return self.coordinator.state_value(self._uid, self._attr_id)

    @property
    def available(self) -> bool:
        return (
            self.coordinator.licensed
            and self.native_value is not None
            and super().available
        )