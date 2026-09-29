"""License client for the RalliSmart custom integration.

The custom never holds PayOS / database / admin secrets: it only calls the
license server's public ``/api/license/activate`` and ``/api/license/verify``
endpoints with this install's stable ``install_id``.

A license key is bound to exactly one Home Assistant install (one custom
copy), which prevents key sharing.
"""
from __future__ import annotations

import logging
import socket
import uuid
from datetime import datetime, timezone
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "rallismart_install"
STORAGE_VERSION = 1


class LicenseError(Exception):
    """Raised when a license cannot be used (invalid/expired/locked/…)."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def default_device_name(hass: HomeAssistant) -> str:
    try:
        name = hass.config.location_name
        if name and name != "Home":
            return name
    except Exception:
        _LOGGER.debug("location_name unavailable", exc_info=True)
    try:
        return socket.gethostname()
    except Exception:  # noqa: BLE001
        return "home-assistant"


async def async_get_install_id(hass: HomeAssistant) -> str:
    """Stable per-install id, persisted in .storage (shared by all entries)."""
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    data = await store.async_load()
    if not isinstance(data, dict) or not data.get("install_id"):
        data = {
            "install_id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await store.async_save(data)
    return str(data["install_id"])


def parse_license_response(data: dict[str, Any]) -> dict[str, Any]:
    """Normalise a server response into a small local state dict."""
    if not isinstance(data, dict):
        return {"valid": False, "code": "bad_response"}
    return {
        "valid": bool(data.get("ok")) and data.get("code") == "active",
        "code": data.get("code") or ("active" if data.get("ok") else "error"),
        "plan": data.get("plan"),
        "expires_at": data.get("expires_at"),
        "key_masked": data.get("key_masked"),
        "token": data.get("token"),
    }


class LicenseManager:
    """Holds license state and talks to the license server."""

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        server: str,
        key: str,
        install_id: str,
    ) -> None:
        self.hass = hass
        self._session = session
        self.server = server.rstrip("/")
        self.key = key
        self.install_id = install_id
        self.device = default_device_name(hass)
        self.valid = False
        self.code = "unverified"
        self.plan: str | None = None
        self.expires_at: str | None = None
        self.key_masked: str | None = None
        self.token: str | None = None

    def _apply(self, state: dict[str, Any]) -> None:
        self.valid = state["valid"]
        self.code = state["code"]
        self.plan = state.get("plan")
        self.expires_at = state.get("expires_at")
        self.key_masked = state.get("key_masked")
        if state.get("token"):
            self.token = state["token"]

    async def _call(self, action: str, timeout: float = 20.0) -> dict[str, Any]:
        url = f"{self.server}/api/license/{action}"
        payload = {"key": self.key, "install_id": self.install_id, "device": self.device}
        if action == "verify" and self.token:
            payload["token"] = self.token
        async with self._session.post(
            url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            data = await resp.json(content_type=None)
        state = parse_license_response(data)
        self._apply(state)
        return state

    async def async_activate(self) -> dict[str, Any]:
        """First activation; raises LicenseError when the key is not usable."""
        try:
            state = await self._call("activate")
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise LicenseError("network", "Không kết nối được máy chủ bản quyền") from err
        if not state["valid"]:
            raise LicenseError(state["code"])
        return state

    async def async_verify(self) -> dict[str, Any]:
        """Heartbeat re-check; returns the state (never raises on invalid)."""
        try:
            return await self._call("verify")
        except (aiohttp.ClientError, TimeoutError, OSError):
            # Transient network issue: keep current state rather than locking out.
            _LOGGER.warning("RalliSmart license server unreachable; keeping last state")
            return {"valid": self.valid, "code": "unreachable"}
