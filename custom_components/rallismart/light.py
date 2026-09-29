"""RalliSmart light platform (Rang Dong HC v1).

On/off (STATUS), brightness (DIM), colour temperature (CCT) and HS colour
(HUE/SATURATION/LUMINANCE) are supported per device capability. CCT is the
app's 0-100 scale mapped linearly onto 2700-6500 K.
"""
from __future__ import annotations

import colorsys
import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .control import (
    ATTR_CCT,
    ATTR_DIM,
    ATTR_HUE,
    ATTR_LUMINANCE,
    ATTR_SATURATION,
    ATTR_STATUS,
    from_255,
    from_kelvin,
    is_light,
    light_capabilities,
    to_255,
    to_kelvin,
)
from .coordinator import RalliSmartCoordinator
from .entity import RalliSmartEntity

_LOGGER = logging.getLogger(__name__)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: RalliSmartCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = [
        RalliSmartLight(coordinator, uid)
        for uid, item in (coordinator.data or {}).items()
        if is_light(item["device"])
    ]
    async_add_entities(entities)


class RalliSmartLight(RalliSmartEntity, LightEntity):
    def __init__(self, coordinator: RalliSmartCoordinator, uid: str) -> None:
        super().__init__(coordinator, uid)
        self._caps = light_capabilities(coordinator.data[uid]["device"])
        modes = {ColorMode.BRIGHTNESS}
        if self._caps["cct"]:
            modes.add(ColorMode.COLOR_TEMP)
        if self._caps["rgb"]:
            modes.add(ColorMode.HS)
        self._attr_supported_color_modes = modes

    def _state(self, attr_id: int) -> Any:
        return self.coordinator.state_value(self._uid, attr_id)

    @property
    def is_on(self) -> bool | None:
        value = self._state(ATTR_STATUS)
        return None if value is None else bool(value)

    @property
    def brightness(self) -> int | None:
        return to_255(self._state(ATTR_DIM))

    @property
    def color_temp_kelvin(self) -> int | None:
        if not self._caps["cct"]:
            return None
        return to_kelvin(self._state(ATTR_CCT))

    @property
    def hs_color(self) -> tuple[float, float] | None:
        if not self._caps["rgb"]:
            return None
        hue = self._state(ATTR_HUE)
        sat = self._state(ATTR_SATURATION)
        if _is_number(hue) and _is_number(sat):
            return (float(hue) % 360, max(0.0, min(100.0, float(sat))))
        return None

    @property
    def color_mode(self) -> ColorMode:
        if self._caps["rgb"] and self._state(ATTR_HUE) is not None:
            return ColorMode.HS
        if self._caps["cct"] and self._state(ATTR_CCT) is not None:
            return ColorMode.COLOR_TEMP
        return ColorMode.BRIGHTNESS

    async def async_turn_on(self, **kwargs: Any) -> None:
        properties: list[dict[str, Any]] = [{"ID": ATTR_STATUS, "VALUE": 1}]
        if ATTR_BRIGHTNESS in kwargs:
            properties.append({"ID": ATTR_DIM, "VALUE": from_255(kwargs[ATTR_BRIGHTNESS])})
        if self._caps["rgb"] and ATTR_HS_COLOR in kwargs:
            hue, sat = kwargs[ATTR_HS_COLOR]
            properties.append({"ID": ATTR_HUE, "VALUE": round(float(hue))})
            properties.append({"ID": ATTR_SATURATION, "VALUE": round(float(sat))})
            properties.append({"ID": ATTR_LUMINANCE, "VALUE": from_255(kwargs.get(ATTR_BRIGHTNESS, 255))})
        elif self._caps["cct"] and ATTR_COLOR_TEMP_KELVIN in kwargs:
            properties.append(
                {"ID": ATTR_CCT, "VALUE": from_kelvin(kwargs[ATTR_COLOR_TEMP_KELVIN])}
            )
        await self.coordinator.async_send_properties(self._uid, properties)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_send_properties(
            self._uid, [{"ID": ATTR_STATUS, "VALUE": 0}]
        )


def hs_to_rgb(hue: float, sat: float) -> tuple[int, int, int]:
    """Helper for tests / diagnostics."""
    r, g, b = colorsys.hsv_to_rgb(hue / 360.0, sat / 100.0, 1.0)
    return (round(r * 255), round(g * 255), round(b * 255))