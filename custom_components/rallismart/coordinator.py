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
    ATTR_STATUS,
    build_device_message,
    cover_properties,
    device_flash_message,
    is_tuya,
    power_properties,
    scene_message,
    tuya_switch_codes,
)
from .hub import RangDongHub, RangDongHubError

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
        self.dormitory_id = dormitory_id
        self.dormitory_name = dormitory_name
        # The first refresh (and the one after a SignalR reconnect) fills state
        # from the cloud so a missed push cannot leave entities stale.
        self._needs_state_sync = True
        self._push_seq = 0
        add_reconnect_listener = getattr(hub, "add_reconnect_listener", None)
        if add_reconnect_listener is not None:
            add_reconnect_listener(self._handle_reconnect)
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
        # Set False by the license heartbeat to disable entities.
        self.licensed = True

    async def _fetch_inventory(self) -> tuple[list, list, list, list]:
        hubs = await self.api.list_home_controllers(self.dormitory_id)
        devices = await self.api.list_devices(self.dormitory_id)
        rooms = await self.api.list_rooms(self.dormitory_id)
        scenes = await self.api.list_scenes(self.dormitory_id)
        return hubs, devices, rooms, scenes

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        if self.hubs:
            try:
                await self.hub.ensure_connected()
            except Exception:
                _LOGGER.debug("hub reconnect failed", exc_info=True)
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
            # Tuya WiFi devices have no Home Controller; they are driven via
            # the cloud proxy and must not be dropped with unsupported HCs.
            if not hc and not tuya_switch_codes(dev):
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
            try:
                attr_id = int(row["deviceAttributeId"])
            except (KeyError, TypeError, ValueError):
                continue
            entry["state"][attr_id] = row.get("value")
        self._needs_state_sync = False

    async def _refresh_tuya_states(self, inventory: dict[str, dict[str, Any]]) -> None:
        """Poll DP values for Tuya WiFi switches (no push channel without HC)."""
        tasks = [
            self._load_tuya_state(entry, str(entry["device"]["tuyaId"]))
            for entry in inventory.values()
            if tuya_switch_codes(entry["device"])
        ]
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
        for pid, members in groups.items():
            parent = by_id.get(pid, members[0])
            members_sorted = sorted(
                members, key=lambda d: (d.get("unicastId") is None, d.get("unicastId") or 0)
            )
            size[pid] = len(members_sorted)
            name[pid] = parent.get("name") or members_sorted[0].get("name") or pid
            pdev[pid] = parent
            for index, member in enumerate(members_sorted, start=1):
                uid = str(member["id"])
                panel_of[uid] = pid
                gang[uid] = index

        self.panel_of = panel_of
        self.panel_gang = gang
        self.panel_size = size
        self.panel_name = name
        self.panel_device = pdev

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

    async def async_set_power(self, uid: str, on: bool) -> None:
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

    async def async_cover(self, uid: str, action: str) -> None:
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

    def _hub_mac_for_scene(self, scene: dict[str, Any]) -> str | None:
        room = self.rooms.get(scene.get("roomId"))
        hc_id = room.get("homeControllerId") if room else None
        hc = self.hubs.get(hc_id) if hc_id else None
        if hc is None and self.hubs:
            hc = next(iter(self.hubs.values()))
        return hc.get("macAddress") if hc else None

    async def async_activate_scene(self, scene_id: str) -> None:
        scene = next((s for s in self.scenes if str(s.get("id")) == str(scene_id)), None)
        if scene is None:
            raise RalliSmartApiError("unknown scene")
        mac = self._hub_mac_for_scene(scene)
        if not mac:
            raise RalliSmartApiError("scene has no home controller")
        await self._send(scene_message(scene, mac))