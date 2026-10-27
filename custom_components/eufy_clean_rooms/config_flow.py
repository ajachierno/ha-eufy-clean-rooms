"""Config flow: Eufy account login, then pick the robot.

MQTT robots come from Eufy's AIOT device list; robots that only live on the
Tuya cloud (S1 Pro and friends) come from the Tuya device list after a Tuya
login with the same account. The user sees one picker and never hears the
word Tuya.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EufyApiError, EufyAuthError, EufyCloud
from .const import (
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_DEVICE_SN,
    CONF_DOCK_HOST,
    CONF_OPENUDID,
    CONF_TRANSPORT,
    CONF_TUYA_REGION,
    DOMAIN,
    TRANSPORT_MQTT,
    TRANSPORT_TUYA,
    TUYA_PRODUCT_MODELS,
)
from .tuya import TuyaError, async_tuya_login

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema({vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str})


class EufyCleanRoomsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._login: dict[str, Any] = {}
        self._devices: dict[str, dict[str, Any]] = {}
        self._details = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EufyCleanRoomsOptionsFlow:
        return EufyCleanRoomsOptionsFlow()

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

    async def _collect_devices(self, cloud: EufyCloud) -> str | None:
        """Fill self._devices from the MQTT and Tuya device lists; return an error key or None."""
        mqtt_devices: list[dict[str, Any]] = []
        if cloud.has_user_center:
            try:
                mqtt_devices = await cloud.get_mqtt_devices()
            except EufyApiError as err:
                self._details = str(err)
                return "cannot_connect"
        names = {
            d.get("id"): d.get("alias_name") or d.get("name")
            for d in await cloud.get_account_devices()
        }
        for dev in mqtt_devices:
            sn = dev.get("device_sn")
            if not sn:
                continue
            self._devices[sn] = {
                CONF_DEVICE_SN: sn,
                CONF_DEVICE_MODEL: dev.get("device_model") or "",
                CONF_DEVICE_NAME: dev.get("device_name") or names.get(sn) or dev.get("device_model") or sn,
                CONF_TRANSPORT: TRANSPORT_MQTT,
            }

        tuya_devices: list[dict[str, Any]] = []
        region = None
        if cloud.user_id:
            try:
                tuya = await async_tuya_login(async_get_clientsession(self.hass), cloud.user_id)
                tuya_devices = await tuya.get_devices()
                region = tuya.region
            except TuyaError as err:
                _LOGGER.warning("Tuya cloud login failed; only MQTT robots can be offered: %s", err)
            except Exception:  # noqa: BLE001 - a Tuya surprise must not block MQTT robots
                _LOGGER.exception("Unexpected error talking to the Tuya cloud; only MQTT robots can be offered")
        for dev in tuya_devices:
            dev_id = dev.get("devId")
            if not dev_id or dev_id in self._devices or not dev.get("localKey"):
                continue
            model = TUYA_PRODUCT_MODELS.get(dev.get("productId") or "") or dev.get("name") or ""
            self._devices[dev_id] = {
                CONF_DEVICE_SN: dev_id,
                CONF_DEVICE_MODEL: model,
                CONF_DEVICE_NAME: dev.get("name") or model or dev_id,
                CONF_TRANSPORT: TRANSPORT_TUYA,
                CONF_TUYA_REGION: region,
            }
        return None if self._devices else "no_devices"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            openudid = uuid.uuid4().hex
            user_input = {**user_input, CONF_EMAIL: user_input[CONF_EMAIL].strip()}
            cloud, error = await self._try_login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD], openudid)
            if cloud:
                error = await self._collect_devices(cloud)
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
            return self.async_create_entry(title=dev[CONF_DEVICE_NAME], data={**self._login, **dev})
        options = {sn: f"{d[CONF_DEVICE_NAME]} ({d[CONF_DEVICE_MODEL]})" for sn, d in self._devices.items()}
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


class EufyCleanRoomsOptionsFlow(OptionsFlow):
    """Tuya robots only: where the dock is on the LAN, when discovery can't find it."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        if entry.data.get(CONF_TRANSPORT) != TRANSPORT_TUYA:
            return self.async_abort(reason="not_tuya")
        if user_input is not None:
            host = (user_input.get(CONF_DOCK_HOST) or "").strip() or None
            self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_DOCK_HOST: host})
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_create_entry(data={})
        schema = vol.Schema({vol.Optional(CONF_DOCK_HOST): str})
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_DOCK_HOST: entry.data.get(CONF_DOCK_HOST) or ""}
            ),
        )
