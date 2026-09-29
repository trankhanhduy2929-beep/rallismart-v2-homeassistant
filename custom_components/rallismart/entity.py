"""Shared base entity that groups multi-gang panels under one HA device."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RalliSmartCoordinator


class RalliSmartEntity(CoordinatorEntity[RalliSmartCoordinator]):
    """Common device_info / naming for panel-based entities.

    A multi-gang wall panel (parent device + ``child NN`` devices) is shown as
    a single HA device; each gang becomes an entity named ``Gang N``. A
    single-gang device keeps its own name.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: RalliSmartCoordinator, uid: str) -> None:
        super().__init__(coordinator)
        self._uid = uid
        panel_id, panel_name, gang, size = coordinator.group(uid)
        parent = coordinator.panel_device.get(panel_id) or coordinator.data[uid]["device"]
        self._attr_unique_id = f"rallismart_{uid}"
        self._attr_name = None if size <= 1 else f"Gang {gang}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"panel_{panel_id}")},
            name=panel_name,
            manufacturer="Rang Dong",
            model=str(parent.get("deviceTypeId") or ""),
        )

    @property
    def available(self) -> bool:
        return (
            self.coordinator.licensed
            and self._uid in (self.coordinator.data or {})
            and super().available
        )
