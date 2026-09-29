"""RalliSmart button platform — device identify (blink) action.

Only the app's verified device-scoped ``DEVICE_FLASH`` action is exposed.
Scene-switch presses (types 230xx/270xx) are physical inputs, not buttons.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .control import is_cover, is_light, is_switch
from .coordinator import RalliSmartCoordinator
from .entity import RalliSmartEntity

_LOGGER = logging.getLogger(__name__)


def _controllable(device: dict[str, Any]) -> bool:
    return is_light(device) or is_switch(device) or is_cover(device)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: RalliSmartCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = [
        RalliSmartIdentifyButton(coordinator, uid)
        for uid, item in (coordinator.data or {}).items()
        if _controllable(item["device"])
    ]
    async_add_entities(entities)


class RalliSmartIdentifyButton(RalliSmartEntity, ButtonEntity):
    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_icon = "mdi:led-on"

    def __init__(self, coordinator: RalliSmartCoordinator, uid: str) -> None:
        super().__init__(coordinator, uid)
        self._attr_name = "Identify"

    async def async_press(self) -> None:
        await self.coordinator.async_flash(self._uid)