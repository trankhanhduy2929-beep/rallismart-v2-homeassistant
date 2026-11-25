"""Coordinator: inventory via REST, live state via the HC SignalR hub."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RalliSmartApi, RalliSmartApiError, RalliSmartAuthError
from .const import DOMAIN, SCAN_INTERVAL_SEC
from .control import (
    ATTR_CCT,
    ATTR_DIM,
    ATTR_HUE,
    ATTR_LUMINANCE,
    ATTR_ON_OFF,
    ATTR_SATURATION,
    ATTR_STATUS,
    build_device_message,
    cover_properties,
    device_flash_message,
    is_camera,
    is_tuya,
    is_tuya_rolling_door,
    power_properties,
    scene_message,
    tuya_switch_codes,
    v2_gang_attr,
    v2_gang_field,
)
from .hub import RangDongHub, RangDongHubError, is_v2
from .mqtt import RangDongMqttError, RangDongMqttHub

_LOGGER = logging.getLogger(__name__)

ENTITY_ATTRIBUTE_VALUE = "HC-DeviceAttributeValue"
ENTITY_DEVICE_RESPONSE = "DeviceResponse"


def coordinators_for(
    hass: HomeAssistant, entry: ConfigEntry
) -> list[RalliSmartCoordinator]:
    """Return every per-home coordinator of a config entry."""
    stored = hass.data.get(DOMAIN, {}).get(entry.entry_id) or {}
    coordinators = stored.get("coordinators")
    if coordinators:
        return list(coordinators.values())
    legacy = stored.get("coordinator")
    return [legacy] if legacy else []


class RalliSmartCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """State per device uid: {"device": {...}, "hc": {...}, "state": {...}}."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: RalliSmartApi,
        hub: RangDongHub,
        dormitory_id: str,
        dormitory_name: str = "",
        config_entry: ConfigEntry | None = None,
        mqtt: RangDongMqttHub | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="rallismart",
            update_interval=timedelta(seconds=SCAN_INTERVAL_SEC),
            config_entry=config_entry,
        )
        self.api = api
        self.hub = hub
        self.mqtt = mqtt
        self.dormitory_id = dormitory_id
        self.dormitory_name = dormitory_name
        # The first refresh (and the one after a SignalR reconnect) fills state
        # from the cloud so a missed push cannot leave entities stale.
        self._needs_state_sync = True
        self._push_seq = 0
        add_reconnect_listener = getattr(hub, "add_reconnect_listener", None)
        if add_reconnect_listener is not None:
            add_reconnect_listener(self._handle_reconnect)
        if mqtt is not None:
            mqtt.add_reconnect_listener(self._handle_reconnect)
        self.hubs: dict[str, dict[str, Any]] = {}
        self.rooms: dict[str, dict[str, Any]] = {}
        self.scenes: list[dict[str, Any]] = []
        # panel grouping: a multi-gang wall panel is one parent device whose
        # children are the extra gangs. Map every uid to its panel.
        self.panel_of: dict[str, str] = {}
        self.panel_gang: dict[str, int] = {}
        self.panel_size: dict[str, int] = {}
        self.panel_name: dict[str, str] = {}
        self.panel_device: dict[str, dict[str, Any]] = {}
        self.panel_member: dict[str, dict[int, str]] = {}
        # Set False by the license heartbeat to disable entities.
        self.licensed = True

    async def _fetch_inventory(self) -> tuple[list, list, list, list]:
        hubs = await self.api.list_home_controllers(self.dormitory_id)
        devices = await self.api.list_devices(self.dormitory_id)
        rooms = await self.api.list_rooms(self.dormitory_id)
        scenes = await self.api.list_scenes(self.dormitory_id)
        return hubs, devices, rooms, scenes

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        if any(not is_v2(hc.get("version")) for hc in self.hubs.values()):
            try:
                await self.hub.ensure_connected()
            except Exception:
                _LOGGER.debug("hub reconnect failed", exc_info=True)
        if self.mqtt is not None and any(
            is_v2(hc.get("version")) for hc in self.hubs.values()
        ):
            try:
                await self.mqtt.ensure_connected()
            except Exception:
                _LOGGER.debug("v2 MQTT reconnect failed", exc_info=True)
        try:
            hubs, devices, rooms, scenes = await self._fetch_inventory()
        except RalliSmartAuthError:
            try:
                await self.api.renew_token()
                hubs, devices, rooms, scenes = await self._fetch_inventory()
            except Exception as err:
                raise UpdateFailed("re-authentication failed") from err
        except RalliSmartApiError as err:
            raise UpdateFailed(str(err)) from err

        self.hubs = {h["id"]: h for h in hubs}
        self.rooms = {r["id"]: r for r in rooms}
        self.scenes = scenes
        inventory: dict[str, dict[str, Any]] = {}
        for dev in devices:
            hc = self.hubs.get(dev.get("homeControllerId"))
            # Tuya WiFi devices and standalone cameras have no Home
            # Controller; they are driven via the cloud and must not be
            # dropped with unsupported HCs.
            if not hc and not (
                tuya_switch_codes(dev) or is_tuya_rolling_door(dev) or is_camera(dev)
            ):
                continue
            uid = str(dev["id"])
            previous = (self.data or {}).get(uid, {}).get("state", {})
            inventory[uid] = {
                "device": dev,
                "hc": hc,
                "state": dict(previous),
                "tuya_state": dict(
                    (self.data or {}).get(uid, {}).get("tuya_state", {})
                ),
            }

        self._build_panels([d for d in devices])
        if self._needs_state_sync:
            await self._sync_cloud_states(inventory)
        await self._refresh_tuya_states(inventory)
        if self.mqtt is not None:
            for hc in self.hubs.values():
                if not is_v2(hc.get("version")):
                    continue
                try:
                    await self.mqtt.request_all_status(hc)
                except Exception:
                    _LOGGER.debug("v2 status poll failed", exc_info=True)
        return inventory

    @callback
    def _handle_reconnect(self) -> None:
        """Reconnect refresh: fetch current values instead of waiting for a push."""
        self._needs_state_sync = True
        self.hass.async_create_task(self.async_request_refresh())

    async def _sync_cloud_states(
        self, inventory: dict[str, dict[str, Any]]
    ) -> None:
        push_seq = self._push_seq
        try:
            rows = await self.api.list_device_attribute_values(self.dormitory_id)
        except Exception:
            _LOGGER.debug("cloud state sync failed", exc_info=True)
            return
        if self._push_seq != push_seq:
            # A live push arrived while the REST snapshot was in flight; keep
            # the newer value and try the catch-up again on the next refresh.
            return
        for row in rows:
            uid = str(row.get("deviceId"))
            entry = inventory.get(uid)
            if not entry or not entry.get("hc"):
                continue
            if (
                is_v2(entry["hc"].get("version"))
                and self.panel_of.get(uid, uid) != uid
            ):
                # Panel child attribute rows are stale in the cloud; the
                # parent's bt/bt2/bt3/bt4 fields are authoritative.
                continue
            try:
                attr_id = int(row["deviceAttributeId"])
            except (KeyError, TypeError, ValueError):
                continue
            entry["state"][attr_id] = row.get("value")
        self._apply_v2_panel_attributes(inventory)
        self._needs_state_sync = False

    def _apply_v2_panel_attributes(
        self, inventory: dict[str, dict[str, Any]]
    ) -> None:
        for pid, members in self.panel_member.items():
            parent = inventory.get(pid)
            if not parent or len(members) < 2:
                continue
            hc = parent.get("hc")
            if not hc or not is_v2(hc.get("version")):
                continue
            for gang, member_uid in members.items():
                value = parent["state"].get(v2_gang_attr(gang))
                member = inventory.get(member_uid)
                if value is None or not member:
                    continue
                member["state"][ATTR_STATUS] = value

    async def _refresh_tuya_states(self, inventory: dict[str, dict[str, Any]]) -> None:
        """Poll DP values for Tuya WiFi devices without a push channel."""
        tasks = []
        for entry in inventory.values():
            device = entry["device"]
            tuya_id = str(device.get("tuyaId") or "")
            if not tuya_id:
                continue
            if tuya_switch_codes(device):
                tasks.append(self._load_tuya_state(entry, tuya_id))
            elif is_tuya_rolling_door(device):
                tasks.append(self._load_tuya_properties(entry, tuya_id))
        if tasks:
            await asyncio.gather(*tasks)

    async def _load_tuya_state(
        self, entry: dict[str, Any], tuya_id: str
    ) -> None:
        try:
            status = await self.api.tuya_get_device_status(tuya_id)
        except Exception:
            _LOGGER.debug("Tuya status failed for %s", tuya_id, exc_info=True)
            return
        if status:
            entry["tuya_state"] = status

    async def _load_tuya_properties(
        self, entry: dict[str, Any], tuya_id: str
    ) -> None:
        try:
            status = await self.api.tuya_get_device_properties(tuya_id)
        except Exception:
            _LOGGER.debug("Tuya properties failed for %s", tuya_id, exc_info=True)
            return
        if status:
            entry["tuya_state"] = status

    def _build_panels(self, devices: list[dict[str, Any]]) -> None:
        """Group multi-gang panels: parent device + its children."""
        by_id = {str(d["id"]): d for d in devices}
        groups: dict[str, list[dict[str, Any]]] = {}
        for dev in devices:
            parent_id = dev.get("parentId")
            # Only treat as a gang when the parent is an actual device in this
            # home; some records point at ids that are not present (e.g. the
            # HC), which must stay standalone.
            key = str(parent_id) if parent_id and str(parent_id) in by_id else str(dev["id"])
            groups.setdefault(key, []).append(dev)

        panel_of: dict[str, str] = {}
        gang: dict[str, int] = {}
        size: dict[str, int] = {}
        name: dict[str, str] = {}
        pdev: dict[str, dict[str, Any]] = {}
        member_map: dict[str, dict[int, str]] = {}
        for pid, members in groups.items():
            parent = by_id.get(pid, members[0])
            members_sorted = sorted(
                members, key=lambda d: (d.get("unicastId") is None, d.get("unicastId") or 0)
            )
            size[pid] = len(members_sorted)
            name[pid] = parent.get("name") or members_sorted[0].get("name") or pid
            pdev[pid] = parent
            member_map[pid] = {}
            for index, member in enumerate(members_sorted, start=1):
                uid = str(member["id"])
                panel_of[uid] = pid
                gang[uid] = index
                member_map[pid][index] = uid

        self.panel_of = panel_of
        self.panel_gang = gang
        self.panel_size = size
        self.panel_name = name
        self.panel_device = pdev
        self.panel_member = member_map

    # ---------- panel helpers ----------

    def group(self, uid: str) -> tuple[str, str, int, int]:
        """Return (panel_id, panel_name, gang_index, panel_size)."""
        pid = self.panel_of.get(uid, uid)
        return pid, self.panel_name.get(pid, uid), self.panel_gang.get(uid, 1), self.panel_size.get(pid, 1)

    # ---------- live updates ----------

    @callback
    def handle_hub_message(self, target: str, args: list[Any]) -> None:
        if target != "Receive" or len(args) < 3:
            return
        entity, payload = args[1], args[2]
        try:
            data = json.loads(payload) if isinstance(payload, str) else payload
        except ValueError:
            return
        if entity == ENTITY_ATTRIBUTE_VALUE:
            self._apply_attribute_values(data)
        elif entity == ENTITY_DEVICE_RESPONSE:
            self._apply_device_response(data)
        elif entity == "Command":
            self._apply_command(data)

    @callback
    def handle_mqtt_message(self, topic: str, message: dict[str, Any]) -> None:
        if message.get("cmd") == "deviceUpdate":
            self._apply_v2_device_update(message.get("data"))

    def _apply_v2_device_update(self, data: Any) -> None:
        if not isinstance(data, dict) or not self.data:
            return
        devices = data.get("device")
        if not isinstance(devices, list):
            return
        changed = False
        for item in devices:
            if not isinstance(item, dict):
                continue
            uid = str(item.get("id") or "")
            fields = item.get("data")
            entry = self.data.get(uid)
            if not uid or not entry or not isinstance(fields, dict):
                continue
            pid, _, gang, size = self.group(uid)
            if size > 1 and pid in self.panel_member:
                if uid == pid:
                    for index, member_uid in self.panel_member[pid].items():
                        field = v2_gang_field(index)
                        if field not in fields:
                            continue
                        value = self._on_value(fields[field])
                        member = self.data.get(member_uid)
                        if member is not None:
                            changed |= self._set_state(member, ATTR_STATUS, value)
                        changed |= self._set_state(entry, v2_gang_attr(index), value)
                    continue
                field = v2_gang_field(gang)
                if field in fields:
                    changed |= self._set_state(
                        entry, ATTR_STATUS, self._on_value(fields[field])
                    )
                elif "onoff" in fields:
                    changed |= self._set_state(
                        entry, ATTR_STATUS, self._on_value(fields["onoff"])
                    )
                continue
            if "bt" in fields:
                changed |= self._set_state(
                    entry, ATTR_STATUS, self._on_value(fields["bt"])
                )
            elif "onoff" in fields:
                changed |= self._set_state(
                    entry, ATTR_STATUS, self._on_value(fields["onoff"])
                )
            if "stt" in fields:
                changed |= self._set_state(
                    entry, ATTR_ON_OFF, self._on_value(fields["stt"])
                )
            for field, attr_id in (
                ("dim", ATTR_DIM),
                ("cct", ATTR_CCT),
                ("h", ATTR_HUE),
                ("s", ATTR_SATURATION),
                ("l", ATTR_LUMINANCE),
            ):
                if field in fields:
                    changed |= self._set_state(entry, attr_id, fields[field])
        if changed:
            self._push_seq += 1
            self.async_set_updated_data(self.data)

    @staticmethod
    def _on_value(value: Any) -> int:
        try:
            return 1 if int(value) else 0
        except (TypeError, ValueError):
            return 1 if value else 0

    def _set_state(
        self, entry: dict[str, Any], attr_id: int, value: Any
    ) -> bool:
        if entry["state"].get(attr_id) == value:
            return False
        entry["state"][attr_id] = value
        return True

    def _apply_attribute_values(self, entries: Any) -> None:
        if not isinstance(entries, list) or not self.data:
            return
        changed = False
        for item in entries:
            uid = str(item.get("deviceId"))
            entry = self.data.get(uid)
            if not entry:
                continue
            entry["state"][int(item["deviceAttributeId"])] = item.get("value")
            changed = True
        if changed:
            self._push_seq += 1
            self.async_set_updated_data(self.data)

    def _apply_command(self, data: Any) -> None:
        """Apply another client's command immediately (e.g. the phone app).

        The hub broadcasts commands to every connection before the HC confirms
        them; using that broadcast removes most of the perceived latency when a
        change originates outside Home Assistant.
        """
        if not isinstance(data, dict) or data.get("CMD") != "DEVICE":
            return
        self._apply_device_response({"DATA": data.get("DATA")})

    def _apply_device_response(self, data: Any) -> None:
        if not self.data or not isinstance(data, dict):
            return
        rows = data.get("DATA")
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            return
        changed = False
        for row in rows:
            uid = str(row.get("DEVICE_ID"))
            entry = self.data.get(uid)
            if not entry:
                continue
            for prop in row.get("PROPERTIES") or []:
                entry["state"][int(prop["ID"])] = prop.get("VALUE")
                changed = True
        if changed:
            self._push_seq += 1
            self.async_set_updated_data(self.data)

    # ---------- state accessors ----------

    def device(self, uid: str) -> dict[str, Any] | None:
        entry = (self.data or {}).get(uid)
        return entry["device"] if entry else None

    def state_value(self, uid: str, attr_id: int) -> Any:
        entry = (self.data or {}).get(uid)
        return entry["state"].get(attr_id) if entry else None

    def is_on(self, uid: str) -> bool | None:
        value = self.state_value(uid, ATTR_STATUS)
        return None if value is None else bool(value)

    def tuya_value(self, uid: str, code: str) -> Any:
        entry = (self.data or {}).get(uid)
        return (entry.get("tuya_state") or {}).get(code) if entry else None

    def is_tuya_on(self, uid: str, code: str) -> bool | None:
        value = self.tuya_value(uid, code)
        return None if value is None else bool(value)

    # ---------- control ----------

    async def async_send_properties(self, uid: str, properties: list[dict[str, Any]]) -> None:
        if not self.licensed:
            raise RalliSmartApiError("License inactive; update License Key in Options")
        entry = (self.data or {}).get(uid)
        if not entry:
            raise RalliSmartApiError("unknown device")
        hc = entry.get("hc")
        if not hc:
            raise RalliSmartApiError("device is not connected to a home controller")
        if is_v2(hc.get("version")):
            await self._send_v2_properties(entry, hc, properties)
            return
        message = build_device_message(
            entry["device"], hc.get("macAddress"), properties
        )
        try:
            await self.hub.send_command(self.dormitory_id, message)
        except RangDongHubError:
            await self.hub.ensure_connected()
            await self.hub.send_command(self.dormitory_id, message)
        # Optimistic; replaced by the next push / DeviceResponse.
        for prop in properties:
            entry["state"][int(prop["ID"])] = prop.get("VALUE")
        self.async_set_updated_data(self.data)

    async def _send_v2_properties(
        self,
        entry: dict[str, Any],
        hc: dict[str, Any],
        properties: list[dict[str, Any]],
    ) -> None:
        if self.mqtt is None:
            raise RalliSmartApiError("HC v2 MQTT transport is not configured")
        fields: dict[str, Any] = {}
        mapping = {
            ATTR_STATUS: "onoff",
            ATTR_DIM: "dim",
            ATTR_CCT: "cct",
            ATTR_HUE: "h",
            ATTR_SATURATION: "s",
            ATTR_LUMINANCE: "l",
        }
        for prop in properties:
            try:
                attr_id = int(prop["ID"])
            except (KeyError, TypeError, ValueError):
                continue
            field = mapping.get(attr_id)
            if field is None:
                raise RalliSmartApiError(
                    f"HC v2 does not support attribute {attr_id} yet"
                )
            value = prop.get("VALUE")
            fields[field] = int(value) if attr_id == ATTR_STATUS else value
        if not fields:
            return
        try:
            await self.mqtt.send_command(
                hc,
                {
                    "cmd": "controlDev",
                    "data": {"id": str(entry["device"]["id"]), "data": fields},
                },
            )
        except RangDongMqttError as err:
            raise RalliSmartApiError(str(err)) from err
        for prop in properties:
            entry["state"][int(prop["ID"])] = prop.get("VALUE")
        self._push_seq += 1
        self.async_set_updated_data(self.data)

    async def async_set_power(self, uid: str, on: bool) -> None:
        if not self.licensed:
            raise RalliSmartApiError("License inactive; update License Key in Options")
        entry = (self.data or {}).get(uid)
        if not entry:
            raise RalliSmartApiError("unknown device")
        hc = entry.get("hc")
        if not hc:
            raise RalliSmartApiError("device is not connected to a home controller")
        if is_v2(hc.get("version")):
            if self.mqtt is None:
                raise RalliSmartApiError("HC v2 MQTT transport is not configured")
            pid, _, gang, size = self.group(uid)
            try:
                await self.mqtt.set_power(
                    hc,
                    str(entry["device"]["id"]),
                    on,
                    panel_id=pid,
                    gang=gang,
                    panel_size=size,
                )
            except RangDongMqttError as err:
                raise RalliSmartApiError(str(err)) from err
            changed = self._set_state(entry, ATTR_STATUS, 1 if on else 0)
            if size > 1:
                parent = self.data.get(pid)
                if parent is not None:
                    changed |= self._set_state(
                        parent, v2_gang_attr(gang), 1 if on else 0
                    )
            if changed:
                self._push_seq += 1
                self.async_set_updated_data(self.data)
            return
        await self.async_send_properties(uid, power_properties(on))

    async def async_set_tuya_switch(self, uid: str, code: str, on: bool) -> None:
        if not self.licensed:
            raise RalliSmartApiError("License inactive; update License Key in Options")
        entry = (self.data or {}).get(uid)
        if not entry or not is_tuya(entry["device"]):
            raise RalliSmartApiError("unknown Tuya device")
        await self.api.tuya_send_commands(
            str(entry["device"]["tuyaId"]), [{"code": code, "value": on}]
        )
        entry.setdefault("tuya_state", {})[code] = on
        self.async_set_updated_data(self.data)

    async def async_set_tuya_property(
        self, uid: str, code: str, value: Any
    ) -> None:
        if not self.licensed:
            raise RalliSmartApiError("License inactive; update License Key in Options")
        entry = (self.data or {}).get(uid)
        if not entry or not is_tuya_rolling_door(entry["device"]):
            raise RalliSmartApiError("unknown Tuya rolling door")
        await self.api.tuya_send_property(
            str(entry["device"]["tuyaId"]), {code: value}
        )
        entry.setdefault("tuya_state", {})[code] = value
        self.async_set_updated_data(self.data)

    async def async_cover(self, uid: str, action: str) -> None:
        entry = (self.data or {}).get(uid)
        if entry and is_tuya_rolling_door(entry["device"]):
            await self.async_set_tuya_property(uid, "control", action)
            return
        await self.async_send_properties(uid, cover_properties(action))

    async def _send(self, message: dict[str, Any]) -> None:
        if not self.licensed:
            raise RalliSmartApiError("License inactive; update License Key in Options")
        try:
            await self.hub.send_command(self.dormitory_id, message)
        except RangDongHubError:
            await self.hub.ensure_connected()
            await self.hub.send_command(self.dormitory_id, message)

    async def async_flash(self, uid: str) -> None:
        entry = (self.data or {}).get(uid)
        if not entry:
            raise RalliSmartApiError("unknown device")
        hc = entry.get("hc")
        if not hc:
            raise RalliSmartApiError("device is not connected to a home controller")
        await self._send(
            device_flash_message(entry["device"], hc.get("macAddress"), on=True)
        )

    # ---------- scenes ----------

    def _hub_for_scene(self, scene: dict[str, Any]) -> dict[str, Any] | None:
        room = self.rooms.get(scene.get("roomId"))
        hc_id = room.get("homeControllerId") if room else None
        hc = self.hubs.get(hc_id) if hc_id else None
        if hc is None and self.hubs:
            hc = next(iter(self.hubs.values()))
        return hc

    async def async_activate_scene(self, scene_id: str) -> None:
        scene = next((s for s in self.scenes if str(s.get("id")) == str(scene_id)), None)
        if scene is None:
            raise RalliSmartApiError("unknown scene")
        hc = self._hub_for_scene(scene)
        if not hc:
            raise RalliSmartApiError("scene has no home controller")
        if is_v2(hc.get("version")):
            if self.mqtt is None:
                raise RalliSmartApiError("HC v2 MQTT transport is not configured")
            try:
                await self.mqtt.control_scene(hc, str(scene["id"]))
            except RangDongMqttError as err:
                raise RalliSmartApiError(str(err)) from err
            return
        mac = hc.get("macAddress")
        if not mac:
            raise RalliSmartApiError("scene has no home controller")
        await self._send(scene_message(scene, mac))
