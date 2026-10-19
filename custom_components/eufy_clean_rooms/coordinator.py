"""Push coordinator: owns the MQTT link, robot state and the room list."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.vacuum import VacuumActivity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import proto
from .api import EufyApiError, EufyAuthError, EufyCloud, EufyMqtt
from .const import (
    CONF_DEVICE_MODEL,
    CONF_DEVICE_SN,
    CONF_OPENUDID,
    DOMAIN,
    DPS_BATTERY,
    DPS_BATTERY_ALT,
    DPS_CLEAN_PARAM,
    DPS_ERROR,
    DPS_FAN_SPEED,
    DPS_FAN_SPEED_ALT,
    DPS_FIND_ROBOT,
    DPS_MODE_CTRL,
    DPS_ROOMS,
    DPS_WORK_STATUS,
    FAN_SPEEDS,
    SIGNAL_ROOMS_UPDATED,
)

_LOGGER = logging.getLogger(__name__)

# MQTT is push; this slow poll only backfills state if a push was missed.
POLL_INTERVAL = timedelta(minutes=10)
STORAGE_VERSION = 1

_ALT_FAN = {"gentle": "Quiet", "normal": "Standard", "strong": "Turbo", "max": "Max"}


@dataclass
class RobotState:
    activity: VacuumActivity | None = None
    task: str = "unknown"
    battery: int | None = None
    fan_speed: str | None = None
    clean_type: str | None = None
    error_codes: list[int] = field(default_factory=list)
    map_id: int = 0
    rooms: list[proto.Room] = field(default_factory=list)
    active_room_ids: list[int] = field(default_factory=list)
    raw_dps: dict[str, Any] = field(default_factory=dict)


class EufyCleanCoordinator(DataUpdateCoordinator[RobotState]):
    """One coordinator per robot."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN} {entry.title}", config_entry=entry,
            update_interval=POLL_INTERVAL,
        )
        self.device_sn: str = entry.data[CONF_DEVICE_SN]
        self.device_model: str = entry.data[CONF_DEVICE_MODEL]
        self.cloud = EufyCloud(
            async_get_clientsession(hass),
            entry.data[CONF_EMAIL],
            entry.data[CONF_PASSWORD],
            entry.data[CONF_OPENUDID],
        )
        self.mqtt: EufyMqtt | None = None
        self.mqtt_connected = False
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{self.device_sn}"
        )
        self.data = RobotState()

    # ------------------------------------------------------------ lifecycle

    async def async_start(self) -> None:
        """Log in, load saved rooms, seed state from the cloud, open MQTT."""
        if saved := await self._store.async_load():
            self.data.map_id = saved.get("map_id", 0)
            self.data.rooms = [proto.Room(r["id"], r["name"]) for r in saved.get("rooms", [])]

        try:
            await self.cloud.login()
            device = await self.cloud.get_device(self.device_sn)
            creds = await self.cloud.get_mqtt_credentials()
        except EufyAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EufyApiError as err:
            raise UpdateFailed(str(err)) from err
        if device is None:
            raise UpdateFailed(
                f"{self.device_sn} is not in the Eufy MQTT device list; this robot "
                "may only be reachable through Tuya, which this integration does not use"
            )

        self.mqtt = EufyMqtt(
            creds, self.cloud.openudid, self.device_sn, self.device_model,
            self._handle_dps, self._handle_connection,
        )
        # Shared robots must be addressed with the owner's account id.
        member = device.get("member") or {}
        owner = member.get("admin_user_id") or member.get("member_user_id")
        if owner and owner != self.mqtt.user_id:
            _LOGGER.debug("Robot is shared; addressing commands as owner %s", owner)
            self.mqtt.user_id = owner

        if dps := device.get("dps"):
            self._apply_dps(dps)
        await self.mqtt.connect()
        self.async_set_updated_data(self.data)

    async def async_stop(self) -> None:
        if self.mqtt:
            await self.mqtt.disconnect()

    async def _async_update_data(self) -> RobotState:
        """Slow backfill poll of the cloud's last-known DPS snapshot."""
        try:
            device = await self.cloud.get_device(self.device_sn)
        except EufyApiError:
            try:
                await self.cloud.login()
                device = await self.cloud.get_device(self.device_sn)
            except EufyAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except EufyApiError as err:
                raise UpdateFailed(str(err)) from err
        if device and (dps := device.get("dps")):
            self._apply_dps(dps)
        return self.data

    # ------------------------------------------------------------ incoming

    @callback
    def _handle_connection(self, connected: bool) -> None:
        self.mqtt_connected = connected
        self.async_update_listeners()

    @callback
    def _handle_dps(self, dps: dict[str, Any]) -> None:
        self._apply_dps(dps)
        self.async_set_updated_data(self.data)

    def _apply_dps(self, dps: dict[str, Any]) -> None:
        state = self.data
        state.raw_dps.update(dps)

        if isinstance(value := dps.get(DPS_WORK_STATUS), str):
            if ws := proto.parse_work_status(value):
                state.activity, state.task = _activity(ws)
                if ws.mode != 1 or ws.state not in (proto.WS_CLEANING, proto.WS_POSITIONING):
                    if state.activity != VacuumActivity.PAUSED:
                        state.active_room_ids = []

        for key in (DPS_BATTERY, DPS_BATTERY_ALT):
            if key in dps:
                try:
                    state.battery = int(dps[key])
                    break
                except (TypeError, ValueError):
                    pass

        if (fan := dps.get(DPS_FAN_SPEED)) is not None:
            state.fan_speed = _fan_name(fan)
        elif (fan := dps.get(DPS_FAN_SPEED_ALT)) is not None:
            state.fan_speed = _ALT_FAN.get(str(fan).lower(), state.fan_speed)

        if isinstance(value := dps.get(DPS_CLEAN_PARAM), str):
            state.clean_type = proto.parse_clean_type(value) or state.clean_type

        if DPS_ERROR in dps:
            state.error_codes = proto.parse_error_code(dps[DPS_ERROR])

        if isinstance(value := dps.get(DPS_ROOMS), str):
            if (table := proto.parse_room_table(value)) and table.rooms:
                self._set_rooms(table)

    def _set_rooms(self, table: proto.RoomTable) -> None:
        state = self.data
        new = [(r.id, r.name) for r in table.rooms]
        if new == [(r.id, r.name) for r in state.rooms] and table.map_id == state.map_id:
            return
        _LOGGER.info("Eufy room list for %s: %s", self.device_sn, [n for _, n in new])
        state.rooms, state.map_id = table.rooms, table.map_id
        self._store.async_delay_save(
            lambda: {"map_id": state.map_id, "rooms": [{"id": i, "name": n} for i, n in new]}, 1
        )
        async_dispatcher_send(self.hass, SIGNAL_ROOMS_UPDATED.format(self.device_sn))

    # ------------------------------------------------------------ outgoing

    async def async_send(self, dps: dict[str, Any]) -> None:
        if not self.mqtt:
            raise HomeAssistantError("Eufy robot is not connected")
        try:
            await self.mqtt.send(dps)
        except EufyApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_mode(self, method: int) -> None:
        await self.async_send({DPS_MODE_CTRL: proto.mode_ctrl(method)})

    async def async_set_fan_speed(self, fan_speed: str) -> None:
        await self.async_send({DPS_FAN_SPEED: _fan_wire(fan_speed, self.data.raw_dps)})

    async def async_locate(self) -> None:
        await self.async_send({DPS_FIND_ROBOT: True})

    def resolve_rooms(self, wanted: list[str | int]) -> list[int]:
        """Turn room names (case-insensitive) or ids into ids, keeping order."""
        by_name = {r.name.casefold(): r.id for r in self.data.rooms}
        by_id = {r.id for r in self.data.rooms}
        ids: list[int] = []
        unknown: list[str] = []
        for item in wanted:
            text = str(item).strip()
            if text.isdigit() and int(text) in by_id:
                ids.append(int(text))
            elif text.casefold() in by_name:
                ids.append(by_name[text.casefold()])
            else:
                unknown.append(text)
        if unknown:
            known = ", ".join(r.name for r in self.data.rooms) or "none yet"
            raise HomeAssistantError(f"Unknown room(s): {', '.join(unknown)}. Known rooms: {known}")
        return list(dict.fromkeys(ids))

    async def async_clean_rooms(
        self,
        room_ids: list[int],
        clean_type: str | None = None,
        fan_speed: str | None = None,
        mop_level: str | None = None,
        clean_times: int = 1,
    ) -> None:
        """Clean the given rooms, optionally overriding vacuum/mop and suction."""
        if not room_ids:
            raise HomeAssistantError("No rooms given")
        if not self.data.rooms:
            raise HomeAssistantError(
                "No room list received from the robot yet; open the map in the Eufy app once"
            )
        if clean_type or mop_level or (fan_speed and _uses_param_fan(self.data.raw_dps)):
            await self.async_send(
                {
                    DPS_CLEAN_PARAM: proto.clean_param_request(
                        clean_type=clean_type,
                        suction=fan_speed if _uses_param_fan(self.data.raw_dps) else None,
                        mop_level=mop_level,
                    )
                }
            )
            await asyncio.sleep(1)
        if fan_speed and not _uses_param_fan(self.data.raw_dps):
            await self.async_set_fan_speed(fan_speed)
            await asyncio.sleep(1)
        await self.async_send(
            {DPS_MODE_CTRL: proto.select_rooms_clean(room_ids, self.data.map_id, clean_times)}
        )
        self.data.active_room_ids = room_ids
        self.async_update_listeners()

    async def async_refresh_rooms(self) -> None:
        """Re-read the cloud snapshot; picks up a room list the robot already reported."""
        await self.async_request_refresh()


def _activity(ws: proto.WorkStatus) -> tuple[VacuumActivity, str]:
    s = ws.state
    if s == proto.WS_FAULT:
        return VacuumActivity.ERROR, "error"
    if s == proto.WS_CHARGING:
        return VacuumActivity.DOCKED, "charged" if ws.charging_state == 1 else "charging"
    if s == proto.WS_GO_HOME:
        return (VacuumActivity.PAUSED, "paused") if ws.paused else (VacuumActivity.RETURNING, "returning")
    if s in (proto.WS_CLEANING, proto.WS_POSITIONING, proto.WS_REMOTE, proto.WS_CRUISING):
        if ws.go_wash_mode in (1, 2):
            return VacuumActivity.DOCKED, "washing mop" if ws.go_wash_mode == 1 else "drying mop"
        if ws.paused:
            return VacuumActivity.PAUSED, "paused"
        task = {1: "room cleaning", 2: "zone cleaning", 3: "spot cleaning"}.get(ws.mode, "cleaning")
        if s == proto.WS_POSITIONING:
            task = "positioning"
        return VacuumActivity.CLEANING, task
    if s == proto.WS_SLEEP:
        return VacuumActivity.IDLE, "sleeping"
    return VacuumActivity.IDLE, "standby"


def _fan_name(value: Any) -> str | None:
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        idx = int(value)
        return FAN_SPEEDS[idx] if 0 <= idx < len(FAN_SPEEDS) else None
    text = str(value).strip().lower()
    return next((s for s in FAN_SPEEDS if s.lower() == text), None)


def _fan_wire(fan_speed: str, raw: dict[str, Any]) -> Any:
    """S1 Pro reports DPS 158 as a word ('Quiet'); X-series as an index."""
    current = raw.get(DPS_FAN_SPEED)
    if isinstance(current, str) and not current.isdigit():
        return fan_speed
    return FAN_SPEEDS.index(fan_speed)


def _uses_param_fan(raw: dict[str, Any]) -> bool:
    """X10 and later carry suction inside CleanParam instead of DPS 158."""
    return DPS_FAN_SPEED not in raw and DPS_CLEAN_PARAM in raw
