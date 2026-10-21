"""Eufy Clean Rooms: cloud MQTT control of Eufy robovacs, including per-room cleaning."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import (
    ATTR_CLEAN_TIMES,
    ATTR_CLEAN_TYPE,
    ATTR_FAN_SPEED,
    ATTR_MOP_LEVEL,
    ATTR_ROOMS,
    CLEAN_TYPE_OPTIONS,
    DOMAIN,
    FAN_SPEEDS,
    SERVICE_CLEAN_ROOMS,
)
from .coordinator import EufyCleanCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [Platform.VACUUM, Platform.BUTTON, Platform.SENSOR]

type EufyCleanConfigEntry = ConfigEntry[EufyCleanCoordinator]

CLEAN_ROOMS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_ROOMS): vol.All(cv.ensure_list, [vol.Any(cv.positive_int, cv.string)]),
        vol.Optional(ATTR_CLEAN_TYPE): vol.In(CLEAN_TYPE_OPTIONS),
        vol.Optional(ATTR_FAN_SPEED): vol.In(FAN_SPEEDS),
        vol.Optional(ATTR_MOP_LEVEL): vol.In(["low", "medium", "high"]),
        vol.Optional(ATTR_CLEAN_TIMES, default=1): vol.All(vol.Coerce(int), vol.Range(1, 3)),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EufyCleanConfigEntry) -> bool:
    coordinator = EufyCleanCoordinator(hass, entry)
    try:
        await coordinator.async_start()
    except UpdateFailed as err:
        await coordinator.async_stop()
        raise ConfigEntryNotReady(str(err)) from err
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EufyCleanConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


def _coordinator_for(hass: HomeAssistant, entity_id: str) -> EufyCleanCoordinator:
    ent = er.async_get(hass).async_get(entity_id)
    if ent is None or ent.platform != DOMAIN or ent.config_entry_id is None:
        raise HomeAssistantError(f"{entity_id} is not an Eufy Clean Rooms vacuum")
    entry = hass.config_entries.async_get_entry(ent.config_entry_id)
    if entry is None or not hasattr(entry, "runtime_data"):
        raise HomeAssistantError(f"{entity_id} is not loaded")
    return entry.runtime_data


def _register_services(hass: HomeAssistant) -> None:
    async def clean_rooms(call: ServiceCall) -> None:
        for entity_id in call.data[ATTR_ENTITY_ID]:
            coordinator = _coordinator_for(hass, entity_id)
            await coordinator.async_clean_rooms(
                coordinator.resolve_rooms(call.data[ATTR_ROOMS]),
                clean_type=call.data.get(ATTR_CLEAN_TYPE),
                fan_speed=call.data.get(ATTR_FAN_SPEED),
                mop_level=call.data.get(ATTR_MOP_LEVEL),
                clean_times=call.data[ATTR_CLEAN_TIMES],
            )

    async def refresh_rooms(call: ServiceCall) -> None:
        for entity_id in call.data[ATTR_ENTITY_ID]:
            await _coordinator_for(hass, entity_id).async_refresh_rooms()

    hass.services.async_register(DOMAIN, SERVICE_CLEAN_ROOMS, clean_rooms, schema=CLEAN_ROOMS_SCHEMA)
    hass.services.async_register(
        DOMAIN, "refresh_rooms", refresh_rooms,
        schema=vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_ids}),
    )
