"""RalliSmart scene platform — activate the app's scenes on the HC."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RalliSmartCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: RalliSmartCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = [
        RalliSmartScene(coordinator, scene)
        for scene in getattr(coordinator, "scenes", [])
        if scene.get("id")
    ]
    async_add_entities(entities)


class RalliSmartScene(CoordinatorEntity[RalliSmartCoordinator], Scene):
    def __init__(self, coordinator: RalliSmartCoordinator, scene: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._scene_id = str(scene["id"])
        self._attr_unique_id = f"rallismart_scene_{self._scene_id}"
        self._attr_name = scene.get("name") or f"Scene {self._scene_id}"

    @property
    def available(self) -> bool:
        return self.coordinator.licensed and super().available

    async def async_activate(self, **kwargs: Any) -> None:
        await self.coordinator.async_activate_scene(self._scene_id)