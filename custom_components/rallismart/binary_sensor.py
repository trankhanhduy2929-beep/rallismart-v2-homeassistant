"""RalliSmart binary_sensor platform (Rang Dong HC v1 sensors)."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .control import ATTR_DOOR, ATTR_PIR, ATTR_SMOKE, sensor_kind
from .coordinator import RalliSmartCoordinator

_LOGGER = logging.getLogger(__name__)

KINDS: dict[str, tuple[int, BinarySensorDeviceClass]] = {
    "smoke": (ATTR_SMOKE, BinarySensorDeviceClass.SMOKE),
    "door": (ATTR_DOOR, BinarySensorDeviceClass.DOOR),
    "motion": (ATTR_PIR, BinarySensorDeviceClass.MOTION),
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
        if kind and kind[1] == "binary_sensor" and kind[0] in KINDS:
            entities.append(RalliSmartBinarySensor(coordinator, uid, kind[0]))
    async_add_entities(entities)


class RalliSmartBinarySensor(CoordinatorEntity[RalliSmartCoordinator], BinarySensorEntity):
    def __init__(self, coordinator: RalliSmartCoordinator, uid: str, kind: str) -> None:
        super().__init__(coordinator)
        self._uid = uid
        self._kind = kind
        self._attr_unique_id = f"rallismart_{uid}_{kind}"
        base = coordinator.data[uid]["device"].get("name")
        self._attr_name = f"{base} {kind}"
        self._attr_id, self._attr_device_class = KINDS[kind]

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.state_value(self._uid, self._attr_id)
        return None if value is None else bool(value)

    @property
    def available(self) -> bool:
        return (
            self.coordinator.licensed
            and self.is_on is not None
            and super().available
        )