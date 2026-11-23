"""Rang Dong HC cloud MQTT client for firmware v2 and newer."""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import uuid
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

from .api import RalliSmartApi
from .control import v2_gang_field

_LOGGER = logging.getLogger(__name__)

MQTT_HOST = "mqtt.rangdong.com.vn"
MQTT_PORT = 8883
MQTT_KEEPALIVE = 30
MQTT_QOS = 2
REQUEST_TIMEOUT = 10.0


class RangDongMqttError(Exception):
    """Raised when the HC v2 MQTT channel rejects a request."""


class RangDongMqttHub:
    """MQTT JSON transport shared by every v2 HC of one account."""

    def __init__(self, api: RalliSmartApi, phone_id: str) -> None:
        self.api = api
        self._phone_id = f"ha-{phone_id}"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: mqtt.Client | None = None
        self._hcs: dict[str, dict[str, Any]] = {}
        self._user_id: str | None = None
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._reconnect_listeners: list[Callable[[], None]] = []
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._connect_future: asyncio.Future[None] | None = None
        self._connect_lock = asyncio.Lock()
        self._connected = False
        self._ever_connected = False
        self._closing = False
        self._warned_tls = False

    def add_listener(self, cb: Callable[[str, dict[str, Any]], None]) -> None:
        self._listeners.append(cb)

    def add_reconnect_listener(self, cb: Callable[[], None]) -> None:
        self._reconnect_listeners.append(cb)

    @property
    def connected(self) -> bool:
        return self._connected and self._client is not None

    async def connect(self, hcs: list[dict[str, Any]]) -> None:
        self._hcs = {
            str(hc["macAddress"]): hc for hc in hcs if hc.get("macAddress")
        }
        if not self._hcs:
            return
        async with self._connect_lock:
            if self.connected:
                self._subscribe_topics()
                return
            self._closing = False
            self._loop = asyncio.get_running_loop()
            old_client = self._client
            if old_client is not None:
                self._client = None
                self._connected = False
                try:
                    old_client.disconnect()
                    await asyncio.to_thread(old_client.loop_stop)
                except Exception:
                    _LOGGER.debug("stale MQTT client close failed", exc_info=True)
            auth = await self.api.mqtt_auth(self._phone_id)
            if not isinstance(auth, dict) or not auth.get("clientId"):
                raise RangDongMqttError("MQTT auth returned no credentials")
            try:
                self._user_id = str(self.api.user["id"])
            except (KeyError, TypeError) as err:
                raise RangDongMqttError("missing account id for MQTT topic") from err
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=str(auth["clientId"]),
                protocol=mqtt.MQTTv311,
            )
            client.username_pw_set(str(auth["username"]), str(auth["password"]))
            if not self._warned_tls:
                _LOGGER.warning(
                    "Rang Dong MQTT broker uses a self-signed certificate; "
                    "TLS certificate verification is disabled"
                )
                self._warned_tls = True
            client.tls_set(
                cert_reqs=ssl.CERT_NONE,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
            client.tls_insecure_set(True)
            client.reconnect_delay_set(min_delay=1, max_delay=30)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            self._client = client
            self._connect_future = self._loop.create_future()
            try:
                await asyncio.to_thread(client.connect, MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
                client.loop_start()
                await asyncio.wait_for(
                    asyncio.shield(self._connect_future), timeout=REQUEST_TIMEOUT
                )
            except Exception:
                self._client = None
                self._connected = False
                future = self._connect_future
                if future is not None and not future.done():
                    future.cancel()
                self._connect_future = None
                try:
                    client.disconnect()
                    await asyncio.to_thread(client.loop_stop)
                except Exception:
                    _LOGGER.debug("MQTT connect cleanup failed", exc_info=True)
                raise

    async def ensure_connected(self) -> None:
        if self.connected:
            return
        if not self._hcs:
            raise RangDongMqttError("no v2 Home Controller registered")
        await self.connect(list(self._hcs.values()))

    async def close(self) -> None:
        self._closing = True
        self._connected = False
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.disconnect()
                await asyncio.to_thread(client.loop_stop)
            except Exception:
                _LOGGER.debug("MQTT close failed", exc_info=True)

    async def request_all_status(self, hc: dict[str, Any]) -> None:
        await self.send_command(
            hc,
            {"cmd": "getAllDevStt", "data": {"time": 0}},
            wait=False,
        )

    async def set_power(
        self,
        hc: dict[str, Any],
        device_id: str,
        on: bool,
        *,
        panel_id: str | None = None,
        gang: int = 1,
        panel_size: int = 1,
    ) -> None:
        value = 1 if on else 0
        if panel_id and panel_size > 1:
            target = panel_id
            fields = {v2_gang_field(gang): value}
        else:
            target = device_id
            fields = {"onoff": value}
        await self.send_command(
            hc,
            {"cmd": "controlDev", "data": {"id": target, "data": fields}},
        )

    async def control_scene(self, hc: dict[str, Any], scene_id: str) -> None:
        await self.send_command(
            hc,
            {"cmd": "controlScene", "data": {"id": scene_id}},
            wait=False,
        )

    async def send_command(
        self,
        hc: dict[str, Any],
        message: dict[str, Any],
        *,
        wait: bool = True,
        timeout: float = REQUEST_TIMEOUT,
    ) -> dict[str, Any] | None:
        mac = str(hc.get("macAddress") or "")
        if mac and mac not in self._hcs:
            self._hcs[mac] = hc
        if not self.connected:
            await self.ensure_connected()
        client = self._client
        if client is None or self._loop is None or self._user_id is None:
            raise RangDongMqttError("MQTT not connected")
        if mac not in self._hcs:
            self._hcs[mac] = hc
            self._subscribe_topic_set(mac)
        rqi = f"mobile-{uuid.uuid4()}"
        payload = dict(message)
        payload["rqi"] = rqi
        topic = f"/v2/mobile/{self._user_id}/hc/{mac}/json_req"
        future: asyncio.Future[dict[str, Any]] | None = None
        if wait:
            future = self._loop.create_future()
            self._pending[rqi] = future
        info = client.publish(topic, json.dumps(payload), qos=MQTT_QOS, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            if future is not None:
                self._pending.pop(rqi, None)
            raise RangDongMqttError(f"MQTT publish failed ({info.rc})")
        if future is None:
            return None
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as err:
            self._pending.pop(rqi, None)
            raise RangDongMqttError("MQTT request timed out") from err
        data = result.get("data")
        if isinstance(data, dict):
            try:
                code = int(data.get("code") or 0)
            except (TypeError, ValueError):
                code = 0
            if code:
                raise RangDongMqttError(
                    f"{payload.get('cmd')} failed with code {code}"
                )
        return result

    def _topic_set(self, mac: str) -> tuple[str, str]:
        return (
            f"/v2/hc/{mac}/mobile/+/json_resp",
            f"/v2/hc/{mac}/mobile/all/json_req",
        )

    def _subscribe_topic_set(self, mac: str) -> None:
        if not self.connected or self._client is None:
            return
        for topic in self._topic_set(mac):
            result, _ = self._client.subscribe(topic, qos=MQTT_QOS)
            if result != mqtt.MQTT_ERR_SUCCESS:
                _LOGGER.warning("MQTT subscribe failed for %s (%s)", topic, result)

    def _subscribe_topics(self) -> None:
        for mac in self._hcs:
            self._subscribe_topic_set(mac)

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        failed = getattr(reason_code, "is_failure", None)
        if failed is None:
            failed = reason_code != 0
        if failed:
            if self._loop is not None and self._connect_future is not None:
                self._loop.call_soon_threadsafe(
                    self._fail_connect,
                    RangDongMqttError(f"MQTT connect rejected ({reason_code})"),
                )
            return
        self._connected = True
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._after_connect)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        self._connected = False
        _LOGGER.debug("MQTT disconnected (%s)", reason_code)
        if self._loop is not None and not self._closing:
            self._loop.call_soon_threadsafe(self._fail_connect, RangDongMqttError("MQTT disconnected"))

    def _fail_connect(self, error: Exception) -> None:
        future = self._connect_future
        if future is not None and not future.done():
            future.set_exception(error)

    def _after_connect(self) -> None:
        self._subscribe_topics()
        future = self._connect_future
        if future is not None and not future.done():
            future.set_result(None)
        if self._ever_connected:
            for cb in list(self._reconnect_listeners):
                try:
                    cb()
                except Exception:
                    _LOGGER.debug("MQTT reconnect listener failed", exc_info=True)
        self._ever_connected = True
        if self._loop is not None:
            self._loop.create_task(self._request_initial_status())

    async def _request_initial_status(self) -> None:
        for hc in list(self._hcs.values()):
            try:
                await self.request_all_status(hc)
            except Exception:
                _LOGGER.debug("initial v2 status request failed", exc_info=True)

    def _on_message(
        self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage
    ) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            self._dispatch, message.topic, bytes(message.payload)
        )

    def _dispatch(self, topic: str, payload: bytes) -> None:
        try:
            text = payload.decode("utf-8")
            message = json.loads(text)
        except (UnicodeDecodeError, ValueError):
            return
        if not isinstance(message, dict):
            return
        rqi = message.get("rqi")
        if rqi:
            future = self._pending.pop(str(rqi), None)
            if future is not None and not future.done():
                future.set_result(message)
        for cb in list(self._listeners):
            try:
                cb(topic, message)
            except Exception:
                _LOGGER.debug("MQTT listener failed", exc_info=True)
