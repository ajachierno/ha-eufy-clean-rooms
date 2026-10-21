"""Config flow: Eufy account login, then pick the robot."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EufyApiError, EufyAuthError, EufyCloud
from .const import CONF_DEVICE_MODEL, CONF_DEVICE_NAME, CONF_DEVICE_SN, CONF_OPENUDID, DOMAIN

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema({vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str})


class EufyCleanRoomsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._login: dict[str, Any] = {}
        self._devices: dict[str, dict[str, Any]] = {}
        self._details = ""

    async def _try_login(self, email: str, password: str, openudid: str) -> tuple[EufyCloud | None, str | None]:
        cloud = EufyCloud(async_get_clientsession(self.hass), email.strip(), password, openudid)
        try:
            await cloud.login()
        except EufyAuthError as err:
            self._details = str(err)
            return None, "invalid_auth"
        except EufyApiError as err:
            self._details = str(err)
            return None, "cannot_connect"
        except Exception as err:  # noqa: BLE001 - surface anything unexpected in the form
            _LOGGER.exception("Unexpected error logging in to Eufy")
            self._details = f"{type(err).__name__}: {err}"
            return None, "unknown"
        return cloud, None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            openudid = uuid.uuid4().hex
            user_input = {**user_input, CONF_EMAIL: user_input[CONF_EMAIL].strip()}
            cloud, error = await self._try_login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD], openudid)
            if cloud:
                try:
                    devices = await cloud.get_mqtt_devices()
                except EufyApiError as err:
                    devices, error = [], "cannot_connect"
                    self._details = str(err)
                if not error:
                    names = {
                        d.get("id"): d.get("alias_name") or d.get("name")
                        for d in await cloud.get_account_devices()
                    }
                    for dev in devices:
                        sn = dev.get("device_sn")
                        if not sn:
                            continue
                        dev[CONF_DEVICE_NAME] = (
                            dev.get("device_name") or names.get(sn) or dev.get("device_model") or sn
                        )
                        self._devices[sn] = dev
                    if not self._devices:
                        error = "no_devices"
            if error:
                errors["base"] = error
            else:
                self._login = {**user_input, CONF_OPENUDID: openudid}
                return await self.async_step_device()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, {CONF_EMAIL: (user_input or {}).get(CONF_EMAIL, "")}
            ),
            errors=errors,
            description_placeholders={"details": self._details},
        )

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None or len(self._devices) == 1:
            sn = user_input[CONF_DEVICE_SN] if user_input else next(iter(self._devices))
            dev = self._devices[sn]
            await self.async_set_unique_id(sn)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=dev[CONF_DEVICE_NAME],
                data={
                    **self._login,
                    CONF_DEVICE_SN: sn,
                    CONF_DEVICE_MODEL: dev.get("device_model") or "",
                    CONF_DEVICE_NAME: dev[CONF_DEVICE_NAME],
                },
            )
        options = {sn: f"{d[CONF_DEVICE_NAME]} ({d.get('device_model')})" for sn, d in self._devices.items()}
        return self.async_show_form(
            step_id="device", data_schema=vol.Schema({vol.Required(CONF_DEVICE_SN): vol.In(options)})
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            _, error = await self._try_login(
                entry.data[CONF_EMAIL], user_input[CONF_PASSWORD], entry.data[CONF_OPENUDID]
            )
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"details": self._details},
        )
