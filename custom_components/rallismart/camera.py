"""RalliSmart camera platform — Dahua/Hikvision RTSP via cloud attribute.

Stream URLs come from ``rpc/iot-ebe/camera-attribute/list``. A camera entity
is only created when the account actually has a stored ``cam_url`` for it.
The URL may embed camera credentials, so it is never logged or exposed in
entity attributes (only returned through ``stream_source``).
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .control import is_camera
from .coordinator import RalliSmartCoordinator

_LOGGER = logging.getLogger(__name__)

_ID_KEYS = ("deviceId", "device_id", "cameraId", "camera_id", "id")
_URL_KEYS = ("cam_url", "camUrl", "Cam_url", "rtspUrl", "url")


def _row_device_id(row: dict[str, Any]) -> str | None:
    for key in _ID_KEYS:
        value = row.get(key)
        if value:
            return str(value)
    return None


def _row_url(row: dict[str, Any]) -> str | None:
    for key in _URL_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.lower().startswith(("rtsp://", "http://", "https://")):
            return value.strip()
    return None


def build_url_map(rows: Any) -> dict[str, str]:
    """Map device ids to validated stream URLs from camera-attribute rows."""
    result: dict[str, str] = {}
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict):
            continue
        device_id = _row_device_id(row)
        url = _row_url(row)
        if device_id and url:
            result[device_id] = url
    return result


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: RalliSmartCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    try:
        rows = await coordinator.api.camera_attributes(coordinator.dormitory_id)
    except Exception:
        _LOGGER.debug("camera attributes unavailable", exc_info=True)
        rows = []
    url_map = build_url_map(rows)
    entities = [
        RalliSmartCamera(coordinator, uid, url_map[uid])
        for uid, item in (coordinator.data or {}).items()
        if is_camera(item["device"]) and uid in url_map
    ]
    async_add_entities(entities)


class RalliSmartCamera(CoordinatorEntity[RalliSmartCoordinator], Camera):
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: RalliSmartCoordinator, uid: str, url: str) -> None:
        Camera.__init__(self)
        super().__init__(coordinator)
        self._uid = uid
        self._stream_url = url
        device = coordinator.data[uid]["device"]
        self._attr_unique_id = f"rallismart_{uid}"
        self._attr_name = device.get("name")
        self._attr_brand = "Dahua" if device.get("deviceTypeId") == 61002 else "Hikvision"

    @property
    def use_stream_for_stills(self) -> bool:
        return True

    async def stream_source(self) -> str | None:
        return self._stream_url

    @property
    def available(self) -> bool:
        return (
            self.coordinator.licensed
            and self._uid in (self.coordinator.data or {})
            and super().available
        )