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

from .control import is_switch, tuya_switch_codes
from .coordinator import RalliSmartCoordinator, coordinators_for
from .entity import RalliSmartEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entities: list[SwitchEntity] = []
    for coordinator in coordinators_for(hass, entry):
        for uid, item in (coordinator.data or {}).items():
            codes = tuya_switch_codes(item["device"])
            if codes:
                for gang, code in enumerate(codes, start=1):
                    entities.append(
                        RalliSmartTuyaSwitch(coordinator, uid, code, gang, len(codes))
                    )
            elif is_switch(item["device"]):
                entities.append(RalliSmartSwitch(coordinator, uid))
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


class RalliSmartTuyaSwitch(RalliSmartEntity, SwitchEntity):
    """One gang of a Tuya WiFi wall switch (no Home Controller)."""

    def __init__(
        self,
        coordinator: RalliSmartCoordinator,
        uid: str,
        code: str,
        gang: int,
        gangs: int,
    ) -> None:
        super().__init__(coordinator, uid)
        self._code = code
        self._attr_unique_id = f"rallismart_{uid}_{code}"
        self._attr_name = None if gangs <= 1 else f"Gang {gang}"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.is_tuya_on(self._uid, self._code)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_tuya_switch(self._uid, self._code, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_tuya_switch(self._uid, self._code, False)
