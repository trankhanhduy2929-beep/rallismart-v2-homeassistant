"""Rang Dong HC SignalR client (legacy v1 firmware).

Firmware < 2.0.0 talks to the cloud via the ``rpc/iot-ebe/signalr/sync``
hub. Commands are sent with hub method ``Send(dormitoryId, "Command", json)``
and responses arrive on ``Receive`` as ``DeviceResponse`` /
``HC-DeviceAttributeValue``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

HUB_PATH = "rpc/iot-ebe/signalr/sync"
ENTITY_COMMAND = "Command"
HUB_V2_VERSION = (2, 0, 0)


class RangDongHubError(Exception):
    pass


def is_v2(version: str | None) -> bool:
    """True when the HC speaks the MQTT v2 protocol (>= 2.0.0)."""
    if not version:
        return False
    parts = []
    for chunk in version.split(".")[:3]:
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts) >= HUB_V2_VERSION


class RangDongHub:
    """SignalR JSON client for one HC home."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        token_provider: Callable[[], str],
        hub_path: str = HUB_PATH,
    ) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._token_provider = token_provider
        self._hub = hub_path.strip("/")
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self._next_id = 1
        self._listeners: list[Callable[[str, list[Any]], None]] = []

    @property
    def _token(self) -> str:
        return self._token_provider()

    def add_listener(self, cb: Callable[[str, list[Any]], None]) -> None:
        self._listeners.append(cb)

    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def ensure_connected(self) -> None:
        """Reconnect if the hub connection dropped (e.g. after sleep)."""
        if not self.connected():
            await self.connect()

    async def connect(self) -> None:
        if self._ws is not None and not self._ws.closed:
            return
        conn_token = await self._negotiate()
        scheme = "wss" if self._base.startswith("https") else "ws"
        host = self._base.split("://", 1)[1]
        url = f"{scheme}://{host}/{self._hub}?id={conn_token}"
        self._ws = await self._session.ws_connect(
            url,
            headers={"Authorization": f"Bearer {self._token}"},
            heartbeat=30,
        )
        await self._ws.send_str('{"protocol":"json","version":1}\x1e')
        msg = await self._ws.receive()
        if msg.type != aiohttp.WSMsgType.TEXT or msg.data.strip("\x1e") not in ("", "{}"):
            raise RangDongHubError("signalr handshake failed")
        self._reader = asyncio.ensure_future(self._read_loop())

    async def _negotiate(self) -> str:
        url = f"{self._base}/{self._hub}/negotiate?negotiateVersion=1"
        async with self._session.post(
            url, headers={"Authorization": f"Bearer {self._token}"}
        ) as resp:
            if resp.status != 200:
                raise RangDongHubError(f"negotiate HTTP {resp.status}")
            data = await resp.json(content_type=None)
        token = data.get("connectionToken") or data.get("connectionId")
        if not token:
            raise RangDongHubError("negotiate returned no connection token")
        return token

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    for frame in msg.data.split("\x1e"):
                        if frame:
                            await self._dispatch(frame)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.debug("signalr reader stopped", exc_info=True)

    async def _dispatch(self, frame: str) -> None:
        try:
            obj = json.loads(frame)
        except ValueError:
            return
        mtype = obj.get("type")
        if mtype == 1:
            args = obj.get("arguments") or []
            for cb in self._listeners:
                try:
                    cb(obj.get("target", ""), args)
                except Exception:
                    _LOGGER.debug("listener error", exc_info=True)
            if obj.get("invocationId") and self._ws is not None:
                await self._ws.send_str(
                    json.dumps({"type": 3, "invocationId": obj["invocationId"]}) + "\x1e"
                )
        elif mtype == 6 and self._ws is not None:
            await self._ws.send_str('{"type":6}\x1e')

    async def send_command(self, dormitory_id: str, message: dict[str, Any]) -> None:
        if self._ws is None or self._ws.closed:
            raise RangDongHubError("hub not connected")
        invocation = {
            "type": 1,
            "invocationId": str(self._next_id),
            "target": "Send",
            "arguments": [dormitory_id, ENTITY_COMMAND, json.dumps(message)],
        }
        self._next_id += 1
        await self._ws.send_str(json.dumps(invocation) + "\x1e")

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
            self._reader = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None