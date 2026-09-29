"""RalliSmart V2 — Rang Dong cloud API (async).

Action names are kebab-case; device access is scoped by ``X-DormitoryId``.
Raw response bodies are never placed into exceptions (they may echo secrets
or data belonging to other accounts).
"""
from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://rallismartv2.rangdong.com.vn"

ROUTE_ACCOUNT = "rpc/iot-ebe/account"
ROUTE_SYNC = "rpc/iot-ebe/sync"
ROUTE_MQTT = "rpc/iot-ebe/mqtt"
ROUTE_CAMERA = "rpc/iot-ebe/camera-attribute"

_ACTION_RE = re.compile(r"[A-Z]")
_SAFE_ERROR = re.compile(r"(/rpc/iot-ebe/[A-Za-z0-9/_-]+)")


def kebab(name: str) -> str:
    return _ACTION_RE.sub(lambda m: "-" + m.group(0).lower(), name).lstrip("-")


class RalliSmartApiError(Exception):
    pass


class RalliSmartAuthError(RalliSmartApiError):
    pass


class RalliSmartApi:
    """Async client for the Rang Dong iot-ebe backend."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str = BASE_URL,
        shared_cookies: bool = False,
    ) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._shared_cookies = shared_cookies
        self.token: str | None = None
        self.refresh_token: str | None = None
        self.user: dict[str, Any] = {}

    @property
    def base_url(self) -> str:
        return self._base

    def _url(self, route: str, action: str = "") -> str:
        route = route.lstrip("/")
        return f"{self._base}/{route}/{kebab(action)}" if action else f"{self._base}/{route}"

    def _cookie_header(self) -> str:
        parts = []
        if self.token:
            parts.append(f"Token={self.token}")
        if self.refresh_token:
            parts.append(f"RefreshToken={self.refresh_token}")
        return "; ".join(parts)

    async def _post(
        self,
        route: str,
        action: str,
        body: Any = None,
        *,
        auth: bool = True,
        headers: dict[str, str] | None = None,
    ) -> Any:
        hdrs = {
            "Content-Type": "application/json",
            "X-Device-Model": "home-assistant",
            "X-OS-Version": "linux",
            "X-Build-Number": "291",
            "X-Build-Version": "1.11.4",
            "X-Brand": "home-assistant",
        }
        hdrs.update(headers or {})
        if auth:
            if not self.token:
                raise RalliSmartAuthError("not authenticated")
            hdrs["Authorization"] = f"Bearer {self.token}"
            cookie = self._cookie_header()
            if cookie and not self._shared_cookies:
                hdrs["Cookie"] = cookie
        async with self._session.post(
            self._url(route, action),
            json=body,
            headers=hdrs,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status in (401, 403):
                raise RalliSmartAuthError(f"auth rejected ({resp.status})")
            if resp.status >= 400:
                text = await resp.text()
                ref = _SAFE_ERROR.search(text or "")
                raise RalliSmartApiError(
                    f"HTTP {resp.status} on {action or route}"
                    + (f" {ref.group(1)}" if ref else "")
                )
            data = await resp.json(content_type=None)
            return data.get("data", data) if isinstance(data, dict) else data

    # ---------- auth ----------

    async def login(
        self, username: str, password: str, device_name: str = "home-assistant"
    ) -> dict[str, Any]:
        body = {"username": username, "password": password, "deviceName": device_name}
        data = await self._post(ROUTE_ACCOUNT, "login", body, auth=False)
        if not isinstance(data, dict) or not data.get("token"):
            raise RalliSmartAuthError("unexpected login response")
        self.user = data.get("user") or {}
        self.token = data["token"]
        self.refresh_token = data.get("refreshToken")
        return data

    async def renew_token(self) -> dict[str, Any]:
        data = await self._post(ROUTE_ACCOUNT, "renewToken")
        if isinstance(data, dict):
            self.token = data.get("token", self.token)
            self.refresh_token = data.get("refreshToken", self.refresh_token)
        return data

    # ---------- scoped discovery ----------

    @staticmethod
    def _filter_body(take: int = 1000) -> dict[str, Any]:
        return {
            "updatedAt": "1970-01-01T00:00:00.000Z",
            "skip": 0,
            "take": take,
            "orderBy": "id",
            "orderType": 0,
        }

    async def list_dormitories(self) -> list[dict[str, Any]]:
        data = await self._post(
            ROUTE_SYNC,
            "listDormitory",
            self._filter_body(),
            headers={"X-DormitoryId": "00000000-0000-0000-0000-000000000000"},
        )
        return [d for d in data if not d.get("deletedAt")] if isinstance(data, list) else []

    async def list_home_controllers(self, dormitory_id: str) -> list[dict[str, Any]]:
        data = await self._post(
            ROUTE_SYNC,
            "listHomeController",
            self._filter_body(),
            headers={"X-DormitoryId": str(dormitory_id)},
        )
        if not isinstance(data, list):
            return []
        return [
            h
            for h in data
            if h.get("dormitoryId") == dormitory_id and not h.get("deletedAt")
        ]

    async def list_devices(self, dormitory_id: str) -> list[dict[str, Any]]:
        """Only devices that belong to one of this home's HC boxes."""
        data = await self._post(
            ROUTE_SYNC,
            "listDevice",
            self._filter_body(),
            headers={"X-DormitoryId": str(dormitory_id)},
        )
        if not isinstance(data, list):
            return []
        hub_ids = {
            h["id"] for h in await self.list_home_controllers(dormitory_id)
        }
        return [
            d
            for d in data
            if d.get("dormitoryId") == dormitory_id
            and d.get("homeControllerId") in hub_ids
            and not d.get("deletedAt")
        ]

    async def list_rooms(self, dormitory_id: str) -> list[dict[str, Any]]:
        data = await self._post(
            ROUTE_SYNC,
            "listRoom",
            self._filter_body(),
            headers={"X-DormitoryId": str(dormitory_id)},
        )
        if not isinstance(data, list):
            return []
        return [r for r in data if r.get("dormitoryId") == dormitory_id and not r.get("deletedAt")]

    async def list_scenes(self, dormitory_id: str) -> list[dict[str, Any]]:
        data = await self._post(
            ROUTE_SYNC,
            "listScene",
            self._filter_body(),
            headers={"X-DormitoryId": str(dormitory_id)},
        )
        if not isinstance(data, list):
            return []
        return [s for s in data if not s.get("deletedAt")]

    # ---------- cameras ----------

    async def camera_attributes(self, dormitory_id: str) -> list[dict[str, Any]]:
        data = await self._post(
            ROUTE_CAMERA,
            "list",
            {},
            headers={"X-DormitoryId": str(dormitory_id)},
        )
        return data if isinstance(data, list) else []

    async def camera_stream_url(self, dormitory_id: str, camera_id: str) -> str | None:
        """Return the stored RTSP URL for one camera, validated.

        The URL may embed camera credentials, so it is never logged or exposed
        through entity attributes.
        """
        data = await self._post(
            ROUTE_CAMERA,
            "get",
            {"Id": camera_id},
            headers={"X-DormitoryId": str(dormitory_id)},
        )
        url = None
        if isinstance(data, dict):
            url = data.get("cam_url") or data.get("Cam_url") or data.get("camUrl")
        if not isinstance(url, str):
            return None
        url = url.strip()
        if not url.lower().startswith(("rtsp://", "http://", "https://")):
            return None
        return url

    # ---------- MQTT (alternate transport, unused by HC v1 path) ----------

    async def mqtt_auth(self, phone_id: str) -> dict[str, Any]:
        return await self._post(ROUTE_MQTT, "auth", {"phoneId": phone_id})