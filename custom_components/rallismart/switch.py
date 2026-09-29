"""RalliSmart switch platform (Rang Dong HC v1).

Multi-gang wall panels are grouped into one HA device; each gang (the panel
parent plus its ``child NN`` devices) is a separate switch entity.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .control import is_switch
from .coordinator import RalliSmartCoordinator
from .entity import RalliSmartEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: RalliSmartCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = [
        RalliSmartSwitch(coordinator, uid)
        for uid, item in (coordinator.data or {}).items()
        if is_switch(item["device"])
    ]
    async_add_entities(entities)


class RalliSmartSwitch(RalliSmartEntity, SwitchEntity):
    def __init__(self, coordinator: RalliSmartCoordinator, uid: str) -> None:
        super().__init__(coordinator, uid)

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.is_on(self._uid)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_power(self._uid, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_power(self._uid, False)