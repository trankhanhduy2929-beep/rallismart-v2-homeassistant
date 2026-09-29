"""Mapping helpers for Rang Dong HC devices (legacy v1 protocol).

Numeric attribute ids come from the bundle's ``Attribute`` map
(``index_decompiled.js`` ~621945), device classes from ``DeviceTypeService``
(``:559586`` onward) and ``DeviceTypeIcon`` (``:562431``).
"""
from __future__ import annotations

from typing import Any

# --- HC v1 attribute ids (cloud SignalR uses the numeric ids) ---
ATTR_STATUS = 0
ATTR_DIM = 1
ATTR_CCT = 2
ATTR_HUE = 3
ATTR_SATURATION = 4
ATTR_LUMINANCE = 5
ATTR_BATTERY = 8
ATTR_LUX = 9
ATTR_PIR = 10
ATTR_PM25 = 18
ATTR_TEMP = 21
ATTR_HUMIDITY = 22
ATTR_BTN_OPEN = 54
ATTR_BTN_CLOSE = 55
ATTR_BTN_PAUSE = 56
ATTR_OPENED = 57
ATTR_SMOKE = 58
ATTR_DOOR = 59
ATTR_ON_OFF = 62
ATTR_MOTOR = 63
ATTR_R = 64
ATTR_G = 65
ATTR_B = 66

DIM_MAX = 100
CCT_MIN_KELVIN = 2700
CCT_MAX_KELVIN = 6500

# --- device classes ---
DOMAIN_LIGHT = "light"
DOMAIN_SWITCH = "switch"
DOMAIN_COVER = "cover"
DOMAIN_BINARY_SENSOR = "binary_sensor"
DOMAIN_SENSOR = "sensor"

_CURTAIN_IDS = frozenset(
    {22006, 22017, 22018, 22024, 22025, 22034, 22035, 22036, 22046, 22047, 45001, 47001}
)
_SWITCH_RANGES = (
    (21001, 21002),
    (22001, 22004),
    (22012, 22015),
    (22019, 22022),
    (22026, 22036),
    (22037, 22039),
    (22040, 22051),
    (24001, 24011),
    (26001, 26003),
    (27001, 27006),
    (42001, 42004),
    (43001, 43004),
    (46001, 46001),
)
_SENSOR_TYPES = {
    33001: ("smoke", "binary_sensor"),
    73001: ("smoke", "binary_sensor"),
    36001: ("door", "binary_sensor"),
    36002: ("door", "binary_sensor"),
    37001: ("pm25", "sensor"),
    38001: ("temperature", "sensor"),
    38002: ("temperature", "sensor"),
    38003: ("humidity", "sensor"),
    39001: ("ph", "sensor"),
    39002: ("ec", "sensor"),
    39003: ("ph", "sensor"),
    39004: ("ec", "sensor"),
    39005: ("oxygen", "sensor"),
}
_CAMERA_TYPES = frozenset({61001, 61002, 61003})


def _type_id(device: dict[str, Any]) -> int:
    try:
        return int(device.get("deviceTypeId") or 0)
    except (TypeError, ValueError):
        return 0


def device_uid(device: dict[str, Any]) -> str:
    return str(device["id"])


def is_camera(device: dict[str, Any]) -> bool:
    return _type_id(device) in _CAMERA_TYPES


def is_cover(device: dict[str, Any]) -> bool:
    return _type_id(device) in _CURTAIN_IDS


def is_light(device: dict[str, Any]) -> bool:
    tid = _type_id(device)
    if tid in _CURTAIN_IDS or tid in _SENSOR_TYPES or tid in _CAMERA_TYPES:
        return False
    major = tid // 1000
    return major in (12, 13, 14, 31) or tid == 13001


def light_capabilities(device: dict[str, Any]) -> dict[str, bool]:
    tid = _type_id(device)
    major = tid // 1000
    rgb = major == 14 or tid == 13001
    cct = major == 12
    return {"brightness": True, "cct": cct, "rgb": rgb}


def is_switch(device: dict[str, Any]) -> bool:
    tid = _type_id(device)
    if is_light(device) or is_cover(device) or tid in _SENSOR_TYPES or tid in _CAMERA_TYPES:
        return False
    return any(lo <= tid <= hi for lo, hi in _SWITCH_RANGES)


def sensor_kind(device: dict[str, Any]) -> tuple[str, str] | None:
    return _SENSOR_TYPES.get(_type_id(device))


def to_kelvin(cct_0_100: Any) -> int | None:
    if not isinstance(cct_0_100, (int, float)):
        return None
    cct = max(0.0, min(100.0, float(cct_0_100)))
    return round(CCT_MIN_KELVIN + cct / 100 * (CCT_MAX_KELVIN - CCT_MIN_KELVIN))


def from_kelvin(kelvin: float) -> int:
    kelvin = max(CCT_MIN_KELVIN, min(CCT_MAX_KELVIN, kelvin))
    return round((kelvin - CCT_MIN_KELVIN) / (CCT_MAX_KELVIN - CCT_MIN_KELVIN) * 100)


def to_255(value_0_100: Any) -> int | None:
    if not isinstance(value_0_100, (int, float)):
        return None
    return max(0, min(255, round(float(value_0_100) * 255 / DIM_MAX)))


def from_255(value_0_255: int) -> int:
    return max(0, min(DIM_MAX, round(value_0_255 * DIM_MAX / 255)))


def build_device_message(
    device: dict[str, Any], mac: str, properties: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build one legacy HC v1 DEVICE message (with TO MAC)."""
    data: dict[str, Any] = {"DEVICE_ID": device["id"], "PROPERTIES": properties}
    if device.get("unicastId") is not None:
        data["DEVICE_UNICAST_ID"] = device["unicastId"]
    return {"CMD": "DEVICE", "DATA": data, "TO": {"MAC": mac}}


def power_properties(on: bool) -> list[dict[str, Any]]:
    return [{"ID": ATTR_STATUS, "VALUE": 1 if on else 0}]


def cover_properties(action: str) -> list[dict[str, Any]]:
    attr = {"open": ATTR_BTN_OPEN, "close": ATTR_BTN_CLOSE, "stop": ATTR_BTN_PAUSE}[action]
    return [{"ID": attr, "VALUE": 1}]


def device_flash_message(device: dict[str, Any], mac: str, on: bool = True) -> dict[str, Any]:
    """Build a ``DEVICE_FLASH`` (identify) message.

    Note: unlike ``DEVICE`` the ``PROPERTIES`` value is a single object.
    """
    data: dict[str, Any] = {
        "DEVICE_ID": device["id"],
        "PROPERTIES": {"ID": ATTR_STATUS, "VALUE": 1 if on else 0},
    }
    if device.get("unicastId") is not None:
        data["DEVICE_UNICAST_ID"] = device["unicastId"]
    return {"CMD": "DEVICE_FLASH", "DATA": data, "TO": {"MAC": mac}}


def scene_message(scene: dict[str, Any], mac: str) -> dict[str, Any]:
    """Build a legacy v1 ``SCENE`` activation message."""
    data: dict[str, Any] = {"SCENE_ID": scene["id"]}
    if scene.get("unicastId") is not None:
        data["SCENE_UNICAST_ID"] = scene["unicastId"]
    return {"CMD": "SCENE", "DATA": data, "TO": {"MAC": mac}}