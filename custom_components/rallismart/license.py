"""License client for the RalliSmart custom integration.

The custom never holds PayOS / database / admin secrets: it only calls the
license server's public ``/api/license/activate`` and ``/api/license/verify``
endpoints with this install's stable ``install_id``.

A license key is bound to exactly one Home Assistant install (one custom
copy), which prevents key sharing.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

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
    except AttributeError:
        _LOGGER.debug("location_name unavailable")
    try:
        return socket.gethostname()
    except Exception:  # noqa: BLE001
        return "home-assistant"


async def async_get_install_id(hass: HomeAssistant) -> str:
    """Stable per-install id, persisted in .storage (shared by all entries)."""
    cached = hass.data.setdefault(STORAGE_KEY, {"lock": asyncio.Lock()})
    async with cached["lock"]:
        if "install_id" not in cached:
            store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
            data = await store.async_load()
            install_id = data.get("install_id") if isinstance(data, dict) else None
            if not isinstance(install_id, str) or not install_id:
                install_id = str(uuid.uuid4())
                await store.async_save({
                    "install_id": install_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            cached["install_id"] = install_id
        return cached["install_id"]


def validate_license_server(server: str) -> str:
    try:
        if (
            not isinstance(server, str) or not 1 <= len(server) <= 2048
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in server)
            or any(c in server for c in "\\?#")
        ):
            raise ValueError
        url = urlsplit(server)
        host = url.hostname
        if (
            url.scheme not in ("https", "http") or not host
            or not re.fullmatch(r"(?:\[[0-9a-fA-F:.]+\]|[a-zA-Z0-9.-]+)(?::[0-9]+)?", url.netloc)
            or (url.port is not None and not 1 <= url.port <= 65535)
        ):
            raise ValueError
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            if len(host) > 253 or not all(
                re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                for label in host.split(".")
            ):
                raise ValueError from None
            loopback = host == "localhost"
        if url.scheme == "http" and not loopback:
            raise ValueError
        return url.geturl().rstrip("/")
    except ValueError:
        raise LicenseError("invalid_server") from None


def parse_license_response(data: Any) -> dict[str, Any]:
    """Normalise a server response into a small local state dict."""
    bad = {"valid": False, "code": "bad_response"}
    if not isinstance(data, dict):
        return bad
    code = data.get("code")
    if data.get("ok") is False:
        codes = (
            "invalid_key", "already_activated", "trial_used", "expired", "locked",
            "revoked", "not_activated", "invalid_token", "token_mismatch",
            "bad_request", "rate_limited", "missing",
        )
        return {"valid": False, "code": code} if code in codes else bad
    if data.get("ok") is not True or code != "active":
        return bad
    for field, limit in (("plan", 128), ("token", 4096)):
        value = data.get(field)
        if not isinstance(value, str) or not 1 <= len(value) <= limit:
            return bad
    if "expires_at" not in data:
        return bad
    expires_at = data["expires_at"]
    if expires_at is not None:
        if not isinstance(expires_at, str) or len(expires_at) > 64:
            return bad
        try:
            expiry = datetime.fromisoformat(expires_at)
            if expiry.tzinfo is None:
                return bad
            if expiry.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                return {"valid": False, "code": "expired"}
        except (ValueError, OverflowError):
            return bad
    masked = data.get("key_masked")
    if masked is not None and (
        not isinstance(masked, str) or len(masked) > 128
    ):
        return bad
    return {
        "valid": True,
        "code": "active",
        "plan": data["plan"],
        "expires_at": expires_at,
        "key_masked": masked,
        "token": data["token"],
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
        self.server = validate_license_server(server)
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise LicenseError("invalid_key")
        self.key = key.strip()
        self.install_id = install_id
        self.device = default_device_name(hass)[:120]
        self._valid = False
        self.code = "unverified"
        self.plan: str | None = None
        self.expires_at: str | None = None
        self.key_masked: str | None = None
        self.token: str | None = None

    @property
    def valid(self) -> bool:
        if (
            self._valid and self.expires_at is not None
            and datetime.now(timezone.utc) >= datetime.fromisoformat(self.expires_at)
        ):
            self._valid = False
            self.code = "expired"
        return self._valid

    def _apply(self, state: dict[str, Any]) -> None:
        self._valid = state["valid"]
        self.code = state["code"]
        self.plan = state.get("plan")
        self.expires_at = state.get("expires_at")
        self.key_masked = state.get("key_masked")
        self.token = state.get("token")

    async def _call(self, action: str, timeout: float = 20.0) -> dict[str, Any]:
        url = f"{self.server}/api/license/{action}"
        payload = {"key": self.key, "install_id": self.install_id, "device": self.device}
        if action == "verify" and self.token:
            payload["token"] = self.token
        try:
            async with self._session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=False,
            ) as resp:
                if resp.status >= 500 or resp.status in (408, 429):
                    raise OSError
                if 300 <= resp.status < 400:
                    return {"valid": False, "code": "bad_response"}
                body = bytearray()
                async for chunk in resp.content.iter_chunked(4096):
                    body.extend(chunk)
                    if len(body) > 16384:
                        return {"valid": False, "code": "bad_response"}
                state = parse_license_response(json.loads(body))
                if not 200 <= resp.status < 300 and state["valid"]:
                    return {"valid": False, "code": "bad_response"}
                return state
        except (ValueError, RecursionError):
            return {"valid": False, "code": "bad_response"}

    async def async_activate(self) -> dict[str, Any]:
        """First activation; raises LicenseError when the key is not usable."""
        try:
            state = await self._call("activate")
        except (aiohttp.ClientError, TimeoutError, OSError):
            raise LicenseError("network") from None
        self._apply(state)
        if not state["valid"]:
            raise LicenseError(state["code"])
        return state

    async def async_verify(self) -> dict[str, Any]:
        """Heartbeat re-check; returns the state (never raises on invalid)."""
        try:
            state = await self._call("verify")
            if state["code"] == "invalid_token":
                state = await self._call("activate")
        except (aiohttp.ClientError, TimeoutError, OSError):
            _LOGGER.warning("RalliSmart license server unreachable")
            valid = self.valid
            return {"valid": valid, "code": "expired" if self.code == "expired" else "unreachable"}
        self._apply(state)
        return state
