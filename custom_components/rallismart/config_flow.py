"""Config flow for RalliSmart V2 (account → home → license)."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RalliSmartApi, RalliSmartApiError, RalliSmartAuthError
from .const import (
    CONF_DEVICE_NAME,
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


class RalliSmartConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._dormitories: list[dict[str, Any]] = []
        self._dormitory: dict[str, Any] | None = None

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
                elif len(self._dormitories) == 1:
                    self._dormitory = self._dormitories[0]
                    return await self.async_step_license()
                else:
                    return await self.async_step_home()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_home(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            dorm = next(
                (d for d in self._dormitories if d["id"] == user_input[CONF_DORMITORY]),
                None,
            )
            if dorm is None:
                return await self.async_step_home()
            self._dormitory = dorm
            return await self.async_step_license()

        options = {d["id"]: d.get("name") or d["id"] for d in self._dormitories}
        schema = vol.Schema({vol.Required(CONF_DORMITORY): vol.In(options)})
        return self.async_show_form(step_id="home", data_schema=schema)

    async def async_step_license(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            install_id = await async_get_install_id(self.hass)
            server = user_input.get(CONF_LICENSE_SERVER) or DEFAULT_LICENSE_SERVER
            manager = LicenseManager(
                self.hass, session, server, user_input[CONF_LICENSE_KEY].strip(), install_id
            )
            try:
                await manager.async_activate()
            except LicenseError as err:
                errors["base"] = "license_network" if err.code == "network" else "invalid_license"
            except Exception:
                _LOGGER.exception("unexpected error during license activation")
                errors["base"] = "unknown"
            else:
                return self._create(user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_LICENSE_KEY): str,
                vol.Optional(CONF_LICENSE_SERVER, default=DEFAULT_LICENSE_SERVER): str,
            }
        )
        return self.async_show_form(step_id="license", data_schema=schema, errors=errors)

    def _create(self, license_input: dict[str, Any]):
        dorm = self._dormitory or self._dormitories[0]
        self._async_abort_entries_match({CONF_DORMITORY: dorm["id"]})
        data = {
            **self._data,
            CONF_DORMITORY: dorm["id"],
            CONF_DORMITORY_NAME: dorm.get("name") or dorm["id"],
            CONF_LICENSE_KEY: license_input[CONF_LICENSE_KEY].strip(),
            CONF_LICENSE_SERVER: license_input.get(CONF_LICENSE_SERVER)
            or DEFAULT_LICENSE_SERVER,
        }
        return self.async_create_entry(
            title=dorm.get("name") or self._data[CONF_USERNAME],
            data=data,
        )
