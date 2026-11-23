"""Config flow for RalliSmart V2 (account → home → license)."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import RalliSmartApi, RalliSmartApiError, RalliSmartAuthError
from .const import (
    ACTIVATE_URL,
    CONF_DEVICE_NAME,
    CONF_DORMITORIES,
    CONF_DORMITORY,
    CONF_DORMITORY_NAME,
    CONF_LICENSE_KEY,
    CONF_LICENSE_SERVER,
    DEFAULT_DEVICE_NAME,
    DEFAULT_LICENSE_SERVER,
    DOMAIN,
)
from .license import LicenseError, LicenseManager, async_get_install_id

_LOGGER = logging.getLogger(__name__)

_LICENSE_ERRORS = {
    "network": "license_network",
    "invalid_server": "invalid_license_server",
    "invalid_key": "license_key_invalid",
    "already_activated": "license_already_activated",
    "trial_used": "license_trial_used",
    "expired": "license_expired",
    "revoked": "license_revoked",
    "locked": "license_locked",
    "bad_request": "license_bad_request",
    "bad_response": "license_bad_response",
    "rate_limited": "license_rate_limited",
}


async def _activate_license(hass: HomeAssistant, user_input: dict[str, Any], server: str):
    try:
        manager = LicenseManager(
            hass, async_get_clientsession(hass), server,
            user_input.get(CONF_LICENSE_KEY, ""), await async_get_install_id(hass),
        )
        await manager.async_activate()
    except LicenseError as err:
        _LOGGER.warning("License activation rejected: %s", err.code)
        return {}, {"base": _LICENSE_ERRORS.get(err.code, "invalid_license")}
    except (OSError, ValueError, RuntimeError, TypeError):
        _LOGGER.error("Unexpected license activation error")
        return {}, {"base": "unknown"}
    return {CONF_LICENSE_KEY: manager.key, CONF_LICENSE_SERVER: manager.server}, {}


def _license_schema() -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_LICENSE_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    })


class RalliSmartConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return RalliSmartOptionsFlow()

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._dormitories: list[dict[str, Any]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            api = RalliSmartApi(session)
            try:
                await api.login(
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    user_input.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME),
                )
                self._dormitories = await api.list_dormitories()
                self._data = user_input
            except RalliSmartAuthError:
                errors["base"] = "invalid_auth"
            except RalliSmartApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("unexpected error during login")
                errors["base"] = "unknown"
            else:
                if not self._dormitories:
                    errors["base"] = "no_homes"
                else:
                    user_id = str(getattr(api, "user", {}).get("id") or "")
                    if user_id:
                        await self.async_set_unique_id(user_id)
                        self._abort_if_unique_id_configured()
                    return await self.async_step_license()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_license(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = await _activate_license(self.hass, user_input, DEFAULT_LICENSE_SERVER)
            if not errors:
                return self._create(data)

        return self.async_show_form(
            step_id="license", data_schema=_license_schema(),
            errors=errors, description_placeholders={"activate_url": ACTIVATE_URL},
        )

    def _create(self, license_input: dict[str, Any]):
        homes = self._dormitories
        self._async_abort_entries_match({CONF_USERNAME: self._data[CONF_USERNAME]})
        names = [home.get("name") or home["id"] for home in homes]
        data = {
            **self._data,
            CONF_DORMITORIES: [home["id"] for home in homes],
            CONF_DORMITORY: homes[0]["id"],
            CONF_DORMITORY_NAME: ", ".join(names),
            CONF_LICENSE_KEY: license_input[CONF_LICENSE_KEY].strip(),
            CONF_LICENSE_SERVER: license_input.get(CONF_LICENSE_SERVER)
            or DEFAULT_LICENSE_SERVER,
        }
        return self.async_create_entry(
            title=", ".join(names) or self._data[CONF_USERNAME],
            data=data,
        )


class RalliSmartOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        entry = self.config_entry
        server = entry.data.get(CONF_LICENSE_SERVER) or DEFAULT_LICENSE_SERVER
        if user_input is not None:
            data, errors = await _activate_license(self.hass, user_input, server)
            if not errors:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, **data},
                )
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_create_entry(title="", data=dict(entry.options))

        return self.async_show_form(
            step_id="init",
            data_schema=_license_schema(),
            errors=errors,
            description_placeholders={"activate_url": ACTIVATE_URL},
        )
