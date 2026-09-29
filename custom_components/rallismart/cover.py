"""RalliSmart cover platform (Rang Dong curtain / rolling door, HC v1)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .control import ATTR_OPENED, is_cover
from .coordinator import RalliSmartCoordinator
from .entity import RalliSmartEntity

_LOGGER = logging.getLogger(__name__)

_ROLL_DOOR_IDS = frozenset({22018, 22025, 22036, 45001})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: RalliSmartCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = [
        RalliSmartCover(coordinator, uid)
        for uid, item in (coordinator.data or {}).items()
        if is_cover(item["device"])
    ]
    async_add_entities(entities)


class RalliSmartCover(RalliSmartEntity, CoverEntity):
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(self, coordinator: RalliSmartCoordinator, uid: str) -> None:
        super().__init__(coordinator, uid)
        device = coordinator.data[uid]["device"]
        try:
            tid = int(device.get("deviceTypeId") or 0)
        except (TypeError, ValueError):
            tid = 0
        self._attr_device_class = (
            CoverDeviceClass.SHUTTER if tid in _ROLL_DOOR_IDS else CoverDeviceClass.CURTAIN
        )

    @property
    def current_cover_position(self) -> int | None:
        value = self.coordinator.state_value(self._uid, ATTR_OPENED)
        return value if isinstance(value, int) else None

    @property
    def is_closed(self) -> bool | None:
        pos = self.current_cover_position
        return None if pos is None else pos == 0

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_cover(self._uid, "open")

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_cover(self._uid, "close")

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_cover(self._uid, "stop")