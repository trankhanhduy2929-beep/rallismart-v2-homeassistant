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
    CONF_DEVICE_NAME,
    CONF_DORMITORY,
    CONF_DORMITORY_NAME,
    CONF_LICENSE_KEY,
    CONF_LICENSE_SERVER,
    DEFAULT_DEVICE_NAME,
    DEFAULT_LICENSE_SERVER,
    DOMAIN,
    WEBSITE_URL,
)
from .license import LicenseError, LicenseManager, async_get_install_id

_LOGGER = logging.getLogger(__name__)


async def _activate_license(hass: HomeAssistant, user_input: dict[str, Any]):
    try:
        manager = LicenseManager(
            hass, async_get_clientsession(hass),
            user_input.get(CONF_LICENSE_SERVER, DEFAULT_LICENSE_SERVER),
            user_input.get(CONF_LICENSE_KEY, ""), await async_get_install_id(hass),
        )
        await manager.async_activate()
    except LicenseError as err:
        return {}, {"base": {
            "network": "license_network", "invalid_server": "invalid_license_server",
        }.get(err.code, "invalid_license")}
    except (OSError, ValueError, RuntimeError, TypeError):
        _LOGGER.error("Unexpected license activation error")
        return {}, {"base": "unknown"}
    return {CONF_LICENSE_KEY: manager.key, CONF_LICENSE_SERVER: manager.server}, {}


def _license_schema(server: str) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_LICENSE_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_LICENSE_SERVER, default=server): str,
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
            data, errors = await _activate_license(self.hass, user_input)
            if not errors:
                return self._create(data)

        return self.async_show_form(
            step_id="license", data_schema=_license_schema(DEFAULT_LICENSE_SERVER),
            errors=errors, description_placeholders={"website_url": WEBSITE_URL},
        )

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


class RalliSmartOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        entry = self.config_entry
        if user_input is not None:
            data, errors = await _activate_license(self.hass, user_input)
            if not errors:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, **data},
                )
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_create_entry(title="", data=dict(entry.options))

        return self.async_show_form(
            step_id="init",
            data_schema=_license_schema(
                entry.data.get(CONF_LICENSE_SERVER) or DEFAULT_LICENSE_SERVER
            ),
            errors=errors,
            description_placeholders={"website_url": WEBSITE_URL},
        )
