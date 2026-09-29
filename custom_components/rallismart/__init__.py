"""RalliSmart V2 custom integration (Rang Dong HC v1 over SignalR)."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import RalliSmartApi, RalliSmartApiError, RalliSmartAuthError
from .const import (
    CONF_DEVICE_NAME,
    CONF_DORMITORY,
    CONF_LICENSE_KEY,
    CONF_LICENSE_SERVER,
    DEFAULT_DEVICE_NAME,
    DEFAULT_LICENSE_SERVER,
    DOMAIN,
    LICENSE_HEARTBEAT_HOURS,
    WEBSITE_URL,
)
from .coordinator import RalliSmartCoordinator
from .hub import RangDongHub, RangDongHubError
from .license import LicenseError, LicenseManager, async_get_install_id

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.LIGHT,
    Platform.SWITCH,
    Platform.COVER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.SCENE,
    Platform.BUTTON,
]


def _notify_license(hass: HomeAssistant, message: str, notification_id: str) -> None:
    try:
        hass.async_create_task(
            hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "RalliSmart V2 — Bản quyền",
                    "message": f"{message}<br/>Mua/gia hạn tại: <a href='{WEBSITE_URL}'>{WEBSITE_URL}</a>",
                    "notification_id": notification_id,
                },
            )
        )
    except Exception:
        _LOGGER.debug("could not create notification", exc_info=True)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    api = RalliSmartApi(session)
    try:
        await api.login(
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            entry.data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME),
        )
    except RalliSmartAuthError as err:
        raise ConfigEntryAuthFailed("invalid credentials") from err
    except RalliSmartApiError as err:
        raise ConfigEntryNotReady("cannot reach RalliSmart") from err

    # ---- License check (no secrets stored locally) ----
    install_id = await async_get_install_id(hass)
    manager = LicenseManager(
        hass,
        session,
        entry.data.get(CONF_LICENSE_SERVER, DEFAULT_LICENSE_SERVER),
        entry.data.get(CONF_LICENSE_KEY, ""),
        install_id,
    )
    try:
        await manager.async_activate()
    except LicenseError as err:
        if err.code == "network":
            raise ConfigEntryNotReady("RalliSmart license server unreachable") from err
        _notify_license(
            hass,
            f"License không hợp lệ ({err.code}). Vui lòng kiểm tra lại key.",
            f"{DOMAIN}_license",
        )
        raise ConfigEntryAuthFailed(f"license {err.code}") from err

    try:
        dormitories = await api.list_dormitories()
    except RalliSmartApiError as err:
        raise ConfigEntryNotReady("cannot list homes") from err
    if not dormitories:
        raise ConfigEntryNotReady("no home available")

    dormitory_id = entry.data.get(CONF_DORMITORY) or dormitories[0]["id"]
    hub = RangDongHub(session, api.base_url, lambda: api.token or "")
    coordinator = RalliSmartCoordinator(hass, api, hub, dormitory_id, config_entry=entry)
    coordinator.licensed = True
    hub.add_listener(coordinator.handle_hub_message)

    try:
        await hub.connect()
    except RangDongHubError as err:
        raise ConfigEntryNotReady("cannot connect to HC hub") from err

    await coordinator.async_config_entry_first_refresh()

    async def _heartbeat(_now) -> None:
        state = await manager.async_verify()
        was = coordinator.licensed
        coordinator.licensed = state["valid"]
        if coordinator.licensed != was:
            coordinator.async_set_updated_data(coordinator.data)
        if not coordinator.licensed:
            _LOGGER.warning("RalliSmart license no longer valid: %s", state["code"])
            _notify_license(
                hass,
                f"License đã hết hạn hoặc bị khóa ({state['code']}).",
                f"{DOMAIN}_license",
            )

    cancel = async_track_time_interval(
        hass, _heartbeat, timedelta(hours=LICENSE_HEARTBEAT_HOURS)
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "hub": hub,
        "coordinator": coordinator,
        "license": manager,
        "cancel_heartbeat": cancel,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data:
            if data.get("cancel_heartbeat"):
                data["cancel_heartbeat"]()
            if data.get("hub"):
                await data["hub"].close()
    return unload_ok
