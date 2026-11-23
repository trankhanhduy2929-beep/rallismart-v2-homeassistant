"""RalliSmart V2 custom integration (Rang Dong HC v1 over SignalR)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_time_interval,
)

from .api import RalliSmartApi, RalliSmartApiError, RalliSmartAuthError
from .const import (
    ACTIVATE_URL,
    CONF_DEVICE_NAME,
    CONF_DORMITORIES,
    CONF_DORMITORY,
    CONF_LICENSE_KEY,
    CONF_LICENSE_SERVER,
    DEFAULT_DEVICE_NAME,
    DEFAULT_LICENSE_SERVER,
    DOMAIN,
    LICENSE_HEARTBEAT_HOURS,
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
    persistent_notification.async_create(
        hass,
        f"{message}\n\n[Kích hoạt / lấy License Key]({ACTIVATE_URL}). "
        f"[Enter / Nhập key: RalliSmart Options](/config/integrations/integration/{DOMAIN}) "
        "— Configure / Cấu hình. Không cần xóa tích hợp; account/home/entity IDs giữ nguyên.",
        title="RalliSmart V2 — License / Bản quyền",
        notification_id=notification_id,
    )


def _select_homes(
    hass: HomeAssistant,
    entry: ConfigEntry,
    dormitories: list[dict],
) -> list[dict]:
    """Homes handled by this entry.

    New entries store every owned home. Legacy entries that stored a single
    home expand to all owned homes unless the user already added several
    entries for the same account; those keep their own home to avoid
    duplicate entities.
    """
    available = {d["id"]: d for d in dormitories}
    configured = entry.data.get(CONF_DORMITORIES)
    if isinstance(configured, list) and configured:
        homes = [available[did] for did in configured if did in available]
        if not homes:
            raise ConfigEntryNotReady("none of the configured homes are available")
        return homes

    siblings = [
        other
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id
        and other.data.get(CONF_USERNAME) == entry.data.get(CONF_USERNAME)
    ]
    if siblings:
        legacy = available.get(entry.data.get(CONF_DORMITORY))
        if legacy is not None:
            _LOGGER.warning(
                "Multiple RalliSmart entries for this account; serving only home %s. "
                "Remove duplicate entries to let one entry manage all homes.",
                legacy["id"],
            )
            return [legacy]
        raise ConfigEntryNotReady(
            "duplicate RalliSmart entries; remove extras and reload"
        )
    return dormitories


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

    manager = None
    notification_id = f"{DOMAIN}_license_{entry.entry_id}"
    if entry.data.get(CONF_LICENSE_KEY):
        try:
            manager = LicenseManager(
                hass,
                session,
                entry.data.get(CONF_LICENSE_SERVER) or DEFAULT_LICENSE_SERVER,
                entry.data[CONF_LICENSE_KEY],
                await async_get_install_id(hass),
            )
            await manager.async_activate()
        except LicenseError as err:
            if err.code == "network":
                raise ConfigEntryNotReady("RalliSmart license server unreachable") from None
            _notify_license(
                hass,
                "License không hợp lệ. Kiểm tra License Key và máy chủ trong Options.",
                notification_id,
            )
            raise ConfigEntryAuthFailed("RalliSmart license invalid; open Options") from None
        persistent_notification.async_dismiss(hass, notification_id)
    else:
        _notify_license(
            hass,
            "Legacy compatibility / Tương thích bản cũ: chưa có License Key; "
            "tích hợp tiếp tục hoạt động như trước nâng cấp. "
            "Cài đặt mới yêu cầu license. Nhập key để chuyển sang kiểm tra bản quyền.",
            notification_id,
        )

    try:
        dormitories = await api.list_dormitories()
    except RalliSmartApiError as err:
        raise ConfigEntryNotReady("cannot list homes") from err
    if not dormitories:
        raise ConfigEntryNotReady("no home available")

    homes = _select_homes(hass, entry, dormitories)
    hub = RangDongHub(session, api.base_url, lambda: api.token or "")
    coordinators: dict[str, RalliSmartCoordinator] = {}
    for home in homes:
        coordinator = RalliSmartCoordinator(
            hass,
            api,
            hub,
            home["id"],
            dormitory_name=(
                (home.get("name") or home["id"]) if len(homes) > 1 else ""
            ),
            config_entry=entry,
        )
        coordinator.licensed = True
        hub.add_listener(coordinator.handle_hub_message)
        coordinators[home["id"]] = coordinator

    for coordinator in coordinators.values():
        await coordinator.async_config_entry_first_refresh()

    if any(coordinator.hubs for coordinator in coordinators.values()):
        try:
            await hub.connect()
        except RangDongHubError as err:
            raise ConfigEntryNotReady("cannot connect to HC hub") from err
    else:
        _LOGGER.info(
            "No Home Controller in %s; using Tuya cloud for WiFi devices",
            ", ".join(home.get("name") or home["id"] for home in homes),
        )

    cancel = None
    cancel_expiry = None
    if manager is not None:
        stopped = False

        @callback
        def _update_license(_now=None) -> None:
            was = all(c.licensed for c in coordinators.values())
            for coordinator in coordinators.values():
                if coordinator.licensed != manager.valid:
                    coordinator.licensed = manager.valid
                    coordinator.async_set_updated_data(coordinator.data)
            if not manager.valid:
                _LOGGER.warning("RalliSmart license no longer valid")
                _notify_license(
                    hass,
                    "License đã hết hạn hoặc không còn hợp lệ. Mở Options để cập nhật.",
                    notification_id,
                )
            elif not was:
                persistent_notification.async_dismiss(hass, notification_id)

        @callback
        def _schedule_expiry() -> None:
            nonlocal cancel_expiry
            if cancel_expiry is not None:
                cancel_expiry()
                cancel_expiry = None
            if manager.valid and manager.expires_at is not None:
                cancel_expiry = async_track_point_in_utc_time(
                    hass, _update_license, datetime.fromisoformat(manager.expires_at)
                )

        async def _heartbeat(_now) -> None:
            if stopped:
                return
            await manager.async_verify()
            if not stopped:
                _update_license()
                _schedule_expiry()

        _update_license()
        _schedule_expiry()
        cancel_interval = async_track_time_interval(
            hass, _heartbeat, timedelta(hours=LICENSE_HEARTBEAT_HOURS)
        )

        @callback
        def cancel() -> None:
            nonlocal stopped
            stopped = True
            cancel_interval()
            if cancel_expiry is not None:
                cancel_expiry()

    first_coordinator = next(iter(coordinators.values()))
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "hub": hub,
        "coordinators": coordinators,
        # Backwards-compatible alias for single-home callers/tests.
        "coordinator": first_coordinator,
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
            for coordinator in data.get("coordinators", {}).values():
                await coordinator.async_shutdown()
            if data.get("hub"):
                await data["hub"].close()
    return unload_ok
